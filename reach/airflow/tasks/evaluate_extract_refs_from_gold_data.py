"""
Operator to fetch and return a json.gz
"""

import json
import gzip
import tempfile

from reach.sentry import report_exception
from airflow.models import BaseOperator
from airflow.utils.decorators import apply_defaults
from reach.airflow.hook.wellcome_s3_hook import WellcomeS3Hook

def _yield_jsonl_from_gzip(fileobj):
    """ Yield a list of dicts read from gzipped json(l)
    """
    with gzip.GzipFile(mode='rb', fileobj=fileobj) as f:
        for line in f:
            yield json.loads(line)

def _get_span_text(text, span):
    return text[span["start"]:span["end"]]

class ExtractRefsFromGoldDataOperator(BaseOperator):
    """ Combine the original validation data and the annotation (gold) data

    NOTE: it may make sense to move this step elsewhere, but for clarity
    I have included it here for now.
    """

    template_fields = (
        'valid_s3_key',
        'gold_s3_key',
        'dst_s3_key',
    )

    @apply_defaults
    def __init__(self, valid_s3_key, gold_s3_key, dst_s3_key,
                 aws_conn_id="aws_default", *args, **kwargs):
        """
        """

        super().__init__(*args, **kwargs)

        self.valid_s3_key = valid_s3_key
        self.gold_s3_key = gold_s3_key
        self.dst_s3_key = dst_s3_key
        self.aws_conn_id = aws_conn_id

    @report_exception
    def execute(self, context):
        # Initialise settings for a limited scraping
        self.log.info("Deciding on policy title")
        s3 = WellcomeS3Hook(aws_conn_id=self.aws_conn_id)

        results = []

        # Download and open the two annotated data files.

        with tempfile.TemporaryFile(mode='rb+') as valid_tf, tempfile.TemporaryFile(mode='rb+') as gold_tf:
            valid_key = s3.get_key(self.valid_s3_key)
            valid_key.download_fileobj(valid_tf)
            valid_tf.seek(0)

            valid = list(_yield_jsonl_from_gzip(valid_tf))

            self.log.info('read %d lines from %s',
                      len(valid),
                      self.valid_s3_key
                      )

            gold_key = s3.get_key(self.valid_s3_key)
            gold_key.download_fileobj(gold_tf)
            gold_tf.seek(0)

            gold = list(_yield_jsonl_from_gzip(gold_tf))

            self.log.info('read %d lines from %s',
                      len(gold),
                      self.gold_s3_key
                      )

        metas = {doc.get('_input_hash'):doc.get('meta') for doc in valid}
        annotated_with_meta = []

        for doc in gold:
            doc["meta"] = metas.get(doc['_input_hash'])
            annotated_with_meta.append(doc)

        # Extract the "Title" and "Document id" from the annotated references

        annotated_titles = []

        for doc in annotated_with_meta:
            doc_hash = None
            meta = doc.get("meta", dict())

            if meta:
                doc_hash = meta.get("doc_hash")
            spans = doc.get("spans")

            if spans:
                for span in spans:
                    annotated_titles.append(
                        {
                            "Document id": doc_hash,
                            "Reference id": None,
                            "Title": _get_span_text(doc["text"], span)
                        }
                    )

        # Write the results to S3
        with tempfile.NamedTemporaryFile(mode='wb') as output_raw_f:
            with gzip.GzipFile(mode='wb', fileobj=output_raw_f) as output_f:
                for item in annotated_titles:
                    output_f.write(json.dumps(item).encode('utf-8'))
                    output_f.write(b"\n")

            output_raw_f.flush()
            s3.load_file(
                filename=output_raw_f.name,
                key=self.dst_s3_key,
                replace=True
            )
            self.log.info(
                'ExtractRefsFromGoldDataOperator: Done extracting refs'
            )

