"""
Operator to run the web scraper on every organisation.
"""
import os
import logging
import tempfile
import json
import gzip

from airflow.models import BaseOperator
from airflow.utils.decorators import apply_defaults

from reach.airflow.hook.wellcome_s3_hook import WellcomeS3Hook
from reach.airflow.safe_import import safe_import
from reach.sentry import report_exception

logger = logging.getLogger(__name__)


class EvaluateOperator(BaseOperator):
    """
    Take the output of fuzz-matched-refs operator and evaluates the results
    against a manually labelled gold dataset, returning results in a json
    to s3.

    Args:
        src_s3_key: S3 URL for input
        dst_s3_key: S3 URL for output
    """

    template_fields = (
        'src_s3_key',
        'dst_s3_key',
    )

    @apply_defaults
    def __init__(self, src_s3_key, dst_s3_key, aws_conn_id='aws_default', *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.src_s3_key = src_s3_key
        self.dst_s3_key = dst_s3_key
        self.aws_conn_id = aws_conn_id

    @report_exception
    def execute(self, context):

        s3 = WellcomeS3Hook(aws_conn_id=self.aws_conn_id)

        results = []

        with tempfile.TemporaryFile(mode='rb+') as tf:

            # Get the precursor file (the fuzzyMatchedRefs)

            key = s3.get_key(self.src_s3_key)
            key.download_fileobj(tf)
            tf.seek(0)

            with gzip.GzipFile(mode='rb', fileobj=tf) as f:

                # Open the gzipped json file

                for fuzzy_matched_ref in f:
                    results.append(fuzzy_matched_ref)

        # Write the results to S3
        with tempfile.NamedTemporaryFile(mode='wb') as output_raw_f:
            with gzip.GzipFile(mode='wb', fileobj=output_raw_f) as output_f:
                for item in results:
                    #output_f.write(item.encode("utf-8"))
                    output_f.write(item)
                    output_f.write(b"\n")

            output_raw_f.flush()
            s3.load_file(
                filename=output_raw_f.name,
                key=self.dst_s3_key,
                replace=True
            )
            logger.info(
                'EvaluateOperator: Finished Evaluation of Matches'
            )
