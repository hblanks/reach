"""
Operator to fetch and return a json.gz
"""

import tempfile
import gzip
import json

from airflow.models import BaseOperator
from airflow.utils.decorators import apply_defaults

from reach.airflow.hook.wellcome_s3_hook import WellcomeS3Hook


def _yield_jsonl_from_gzip(fileobj):
    """ Yield a list of dicts read from gzipped json(l)
    """
    with gzip.GzipFile(mode='rb', fileobj=fileobj) as f:
        for line in f:
            yield json.loads(line)

class FetchJsonGzip(BaseOperator):
    """ Download a json(l).gz and return as a list`.
    """

    template_fields = (
        'src_s3_key',
    )

    @apply_defaults
    def __init__(self, src_s3_key, aws_conn_id='aws_default', *args, **kwargs):
        """
        Args:
            src_s3_key: S3 URL for the json.gz output file.
            aws_conn_id: Aws connection name.
        """

        super().__init__(*args, **kwargs)

        if not src_s3_key.endswith(('.json.gz', '.jsonl.gz')):
            raise ValueError('src_s3_key must end in .json.gz or .jsonl.gz')

        self.src_s3_key = src_s3_key
        self.aws_conn_id = aws_conn_id

    def execute(self, context):
        s3 = WellcomeS3Hook()

        self.log.info(
            'Getting data from %s',
            self.src_s3_key,
        )

        # Download S3 object
        s3_object = s3.get_key(self.src_s3_key)
        with tempfile.NamedTemporaryFile() as tf:
            s3_object.download_fileobj(tf)
            tf.seek(0)

            # Open the gzip and read the jsonl

            results = list(_yield_jsonl_from_gzip(tf))

            print(results)


        #self.log.info('execute: insert complete count=%d', count)
