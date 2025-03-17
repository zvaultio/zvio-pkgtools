import logging
import logging.config
import math
import syslog
import sys
import select
import subprocess

# To use this:
# from . import Avatar
# os_type = Avatar()

# We may want to have more
# platform-specific stuff.

# Sef likes this line a lot.
_os_type = "TrueNAS"
UPDATE_SERVER = "https://update.zvalut.io/" + _os_type
MASTER_UPDATE_SERVER = "https://update-master.zvalut.io//" + _os_type

# For signature verification
IX_CRL = "https://update-master.zvalut.io/updates/ix_crl.pem"
DEFAULT_CA_FILE = "/usr/local/share/certs/ca-root-nss.crt"
IX_ROOT_CA_FILE = "/usr/local/share/certs/iX-CA.pem"
UPDATE_CERT_DIR = "/usr/local/share/certs"
UPDATE_CERT_PRODUCTION = UPDATE_CERT_DIR + "/Production.pem"
UPDATE_CERT_NIGHTLIES = UPDATE_CERT_DIR + "/Nightlies.pem"
VERIFIER_HELPER = "/usr/local/libexec/verify_signature"
SIGNATURE_FAILURE = True


def Avatar():
    return _os_type


# Note: logger.hasHandlers was added in python 3.2 so backporting
# this function here so that python2.7 can also use it.
def hasHandlers(logger):
    """
    See if this logger has any handlers configured.

    Loop through all handlers for this logger and its parents in the
    logger hierarchy. Return True if a handler was found, else False.
    Stop searching up the hierarchy whenever a logger with the "propagate"
    attribute set to zero is found - that will be the last logger which
    is checked for the existence of handlers.
    """
    c = logger
    rv = False
    while c:
        if c.handlers:
            rv = True
            break
        if not c.propagate:
            break
        else:
            c = c.parent
    return rv


def modified_call(popenargs, logger, **kwargs):
    """
    Variant of subprocess.call that accepts a logger instead of stdout/stderr,
    and logs stdout messages via logger.debug and stderr messages via
    logger.error.

    Original code taken from: https://gist.github.com/hangtwenty/6390750 and modified
    """
    preexec_fn = kwargs.get('preexec_fn')
    stdout_log_level = kwargs.get('stdout_log_level', logging.DEBUG)
    stderr_log_level = kwargs.get('stderr_log_level', logging.ERROR)
    env = kwargs.get('env')
    proc = subprocess.Popen(
        popenargs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=preexec_fn, env=env
    )

    try:
        log_level = {
            proc.stdout: stdout_log_level,
            proc.stderr: stderr_log_level
        }

        streams = [proc.stdout, proc.stderr]

        while streams:
            ready_to_read, _, _ = select.select(streams, [], [])
            for io in ready_to_read:
                text = io.readline().decode('utf8')
                if text == '':
                    streams.remove(io)
                    continue

                text = text.strip()
                if text:
                    logger.log(log_level[io], text)

        return proc.wait()
    finally:
        proc.stdout.close()
        proc.stderr.close()


class SysLogHandler(logging.Handler):

    priority_names = {
        "alert": syslog.LOG_ALERT,
        "crit": syslog.LOG_CRIT,
        "critical": syslog.LOG_CRIT,
        "debug": syslog.LOG_DEBUG,
        "emerg": syslog.LOG_EMERG,
        "err": syslog.LOG_ERR,
        "error": syslog.LOG_ERR,
        "info": syslog.LOG_INFO,
        "notice": syslog.LOG_NOTICE,
        "panic": syslog.LOG_EMERG,
        "warn": syslog.LOG_WARNING,
        "warning": syslog.LOG_WARNING,
    }

    def __init__(self, facility=syslog.LOG_USER):
        self.facility = facility
        super(SysLogHandler, self).__init__()

    def emit(self, record):
        """
        syslog has a character limit per message
        split the message in chuncks

        The value of 950 is a guess based on tests,
        it could be a little higher.
        """
        syslog.openlog(facility=self.facility)
        msg = self.format(record)
        num_msgs = int(math.ceil(len(msg) / 950.0))
        for i in range(num_msgs):
            if num_msgs == i - 1:
                _msg = msg[950 * i:]
            else:
                _msg = msg[950 * i:950 * (i + 1)]
            syslog.syslog(
                self.priority_names.get(record.levelname.lower(), syslog.LOG_DEBUG),
                _msg
            )
        syslog.closelog()


class StartsWithFilter(logging.Filter):
    def __init__(self, **kwargs):
        self.module = kwargs.get('module', '')
        self.params = kwargs.get('params', [])

    def filter(self, record):
        if self.params:
            allow = not any(
                record.name.startswith(self.module) and record.msg.startswith(x) for x in self.params
            )
        else:
            allow = True
        return allow


test_logger = logging.getLogger(__name__)

log_config_dict = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '[%(name)s:%(lineno)s] %(message)s',
        },
    },
    'handlers': {
        'stderr': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'stream': 'ext://sys.stderr',
            'formatter': 'simple'
        },
        'stdout': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'stream': 'ext://sys.stdout',
            'formatter': 'simple'
        },
        'syslog': {
            'level': 'DEBUG',
            'class': 'freenasOS.SysLogHandler',
            'formatter': 'simple'
        }
    }
}


def disable_trygetfilelogs():
    update_log_filter = StartsWithFilter(
        module='freenasOS', params=['TryGetNetworkFile', 'Searching']
    )
    for handler in logging.root.handlers:
        handler.addFilter(update_log_filter)


def log_to_handler(specified_handler):
    """
    Switch freenasOS logging to either one of the following handlers:
        1. 'stdout'
        2. 'stderr'
        3. 'syslog'
    """
    log_config_dict['loggers'] = {
        '': {
            'handlers': [specified_handler],
            'level': 'DEBUG',
            'propagate': True
        }
    }
    logging.config.dictConfig(log_config_dict)


if not hasHandlers(test_logger):
    log_config_dict['loggers'] = {
        '': {
            'handlers': ['syslog'],
            'level': 'DEBUG',
            'propagate': True
        }
    }
    logging.config.dictConfig(log_config_dict)
