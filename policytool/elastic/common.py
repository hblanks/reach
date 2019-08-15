from argparse import ArgumentParser
from urllib.parse import urlparse
import gzip
import logging
import tempfile
import time

import boto3
import elasticsearch
import elasticsearch.helpers

import policytool.logging

CHUNK_SIZE = 1000
MAX_RETRIES = 10
MAX_ITEMS_PER_POOL = 50000

WORKER_COUNT = 6
QUEUE_SIZE = 6

# ES logs excessively by default. Don't let it do that.
es_logger = logging.getLogger('elasticsearch')
es_logger.setLevel(logging.WARNING)


class IndexingErrorException(Exception):
    """Exception to raise if any error happens during elasticsearch
       bulk indexing.
    """
    pass


def yield_actions(tf, to_es_action, max_items=None):
    """ Given a file containing gzipped data, yield back actions
    for insertion into ES.

    Args:
        tf: file object containing newline-delimited JSON
        to_es_action:
            function converting uncompressed line from file into an
            action for ES.
        max_items: maximum number of items to yield

    Yields:
        dicts of actions for ES bulk API
    """
    logging.info(
        'yield_actions: tf=%s to_es_action=%s max_items=%s',
        tf, to_es_action, max_items)

    with gzip.GzipFile(fileobj=tf, mode='r') as json_file:
        count = 0
        for count, line in enumerate(json_file, 1):
            yield to_es_action(line.decode('utf-8'))
            if max_items is not None and count >= max_items:
                break
        logging.info('yield_actions: count=%d', count)


def clean_es(es, index_name):
    """ Empty the elasticsearch database.

    Args:
        es: a living connection to elasticsearch
    """
    logging.info('clean_es: index_name=%s', index_name)
    # Ignore if the index doesn't exist, as it'll be created by next queries
    es.indices.delete(index=index_name, ignore=[404])
    es.indices.create(index=index_name)


def count_es(es, index_name):
    """ Returns number of documents in an index. """
    return es.count(index=index_name)['count']


def _n_actions(actions, max_items):
    """ Yield only max_items actions before exiting. """
    for count, action in enumerate(actions, 1):
        yield action
        if count >= max_items:
            return


def insert_actions(es, actions):
    """ Inserts an iterable of actions into an ES cluster. """
    # Why this loops: once an exception occurs in bulk API calls, memory
    # starts to grow rapidly, presumably because frames start
    # accumulating within elasticsearch.helpers.parallel_bulk() or
    # streaming_bulk() as connect exceptions / retries pile up.
    # Typically this would manifest as the first ~200K inserts going
    # fine, with memory <400MB, but as ES starts to slow down (and
    # retries become necessary), our python process starts ballooning,
    # reaching 4GB well before we get to even 3M records.
    # 
    # Thus, we only call parallel_bulk() in chunks of MAX_ITEMS_PER_POOL
    # before calling it again, giving the reference counter a chance to
    # free up memory from the call stack from time to time. Note also
    # that retries are handled by the ES connection itself, so we can
    # get away with using parallel_bulk(), which has no retry logic of
    # its own.
    start = time.time()
    total_count = 0
    loop_count = None
    while loop_count != 0:
        loop_count = 0
        n_actions = _n_actions(actions, MAX_ITEMS_PER_POOL)
        tups = elasticsearch.helpers.parallel_bulk(
                es, n_actions,
                chunk_size=CHUNK_SIZE,
                thread_count=WORKER_COUNT,
                queue_size=QUEUE_SIZE,
                raise_on_error=False,
                )
        for ok, result in tups:
            if not ok:
                logging.error(
                    "insert_actions: failure results=%s",
                    json.dumps(results))
                raise IndexingErrorException

            total_count += 1
            if total_count % 10000 == 0:
                logging.info("insert_actions: count=%d", total_count)
            
            loop_count += 1
        logging.info("insert_actions: loop_count=%d", loop_count)

    finish = time.time()
    rate = total_count / (finish - start)
    logging.info(
        "insert_actions: completed count=%d duration=%.2f rate=%d items/sec",
        total_count, finish - start, rate)

    return total_count


def create_argument_parser(description):
    parser = ArgumentParser(description)
    parser.add_argument('-H', '--host',
                        default='127.0.0.1',
                        help='Address of the Elasticsearch server')

    parser.add_argument('-C', '--clean', dest='clean', action='store_true',
                        help='Clean the elasticsearch database before import')

    parser.add_argument('-P', '--port',
                        default='9200',
                        help='Port of the Elasticsearch server')
    return parser


def connect(host, port):
    return elasticsearch.Elasticsearch(
        [{'host': host, 'port': port}], timeout=60, retries=5,
        retry_on_timeout=True)


def es_from_args(args):
    return connect(args.host, args.port)

def insert_from_argv(description, clean_es, insert_file):
    parser = create_argument_parser(description)
    parser.add_argument('input',
                        help='Local gzipped JSON URL (or S3 URL)')

    parser.add_argument('--max-items',
                        default=None,
                        type=int,
                        help='Number of record to insert. Default: all.')
    args = parser.parse_args()

    policytool.logging.basicConfig()

    es = es_from_args(args)

    if args.clean:
        clean_es(es)

    if args.input.startswith('s3://'):
        parsed_url = urlparse(args.input)
        logging.info(
            'Getting %s from %s bucket',
            parsed_url.path, parsed_url.netloc)

        s3 = boto3.resource('s3')
        s3_file = s3.Object(
            bucket_name=parsed_url.netloc,
            key=parsed_url.path[1:]
        )
        with tempfile.NamedTemporaryFile() as f:
            s3_file.download_fileobj(f)
            f.seek(0)
            return insert_file(f, es, args.max_items)
    else:
        with open(args.input, 'rb') as f:
            return insert_file(f, es, args.max_items)
