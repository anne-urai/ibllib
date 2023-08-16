"""Library implementing the International Brain Laboratory data pipeline."""
import logging
import warnings

__version__ = '2.25.0-dev5+mesoscopeBrainLocation'
warnings.filterwarnings('always', category=DeprecationWarning, module='ibllib')

# if this becomes a full-blown library we should let the logging configuration to the discretion of the dev
# who uses the library. However since it can also be provided as an app, the end-users should be provided
# with an useful default logging in standard output without messing with the complex python logging system
# -*- coding:utf-8 -*-
USE_LOGGING = True
#%(asctime)s,%(msecs)d
if USE_LOGGING:
    from iblutil.util import setup_logger
    setup_logger(name='ibllib', level=logging.INFO)
else:
    # deactivate all log calls for use as a library
    logging.getLogger('ibllib').addHandler(logging.NullHandler())
