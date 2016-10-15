#!/usr/bin/env python
# -*- coding: utf-8 -*-
#

"""Allow user to run ScarlettOS as a module."""

# Execute with:
# $ python -m scarlett_os

import scarlett_os
import scarlett_os.cli


if __name__ == '__main__':
    scarlett_os.cli.main()
