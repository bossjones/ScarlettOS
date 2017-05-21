"""Generic testing utility tools."""

import tempfile
import contextlib

# source: Timeside
def tmp_file_sink(prefix=None, suffix=None):
    tmpfile = tempfile.NamedTemporaryFile(delete=True,
                                          prefix=prefix,
                                          suffix=suffix)
    tmpfile.close()
    return tmpfile.name
