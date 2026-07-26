"""
setup_logger.py - configure file and console logging for a program
Creates a timestamped log file in the given directory and attaches a
console handler for INFO-and-above messages. Called once at program
startup from setup_args(); thereafter any logger obtained via
logging.getLogger(__name__) writes to both the log file and the console.
=====================
INPUT ARGS:
program     name of the calling program (a trailing .py is stripped)
logdir      directory for the log file (None writes to the current dir)
loglevel    log level name: DEBUG, INFO, WARNING, ERROR, or CRITICAL
"""


import logging
from datetime import datetime


def setup_logger(program, logdir, loglevel):
    """
    Configure root logging to a timestamped file and attach an INFO-level
    console handler. Return the module logger.
    """
    logger = logging.getLogger(__name__)
    current_datetime = datetime.now()
    time = current_datetime.strftime('%y%m%d.%H%M%S')
    if program.endswith('.py'):
        program = program[:-3]
    logfile = program + '.' + time + '.log'
    if logdir is None:
        logpath = logfile
    else:
        logpath = logdir + '/' + logfile
    numeric_level = getattr(logging, loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f'Invalid log level: {loglevel}')
    formatstr = '%(asctime)s %(levelname)s - %(message)s'
    formatter = logging.Formatter(formatstr)
    logging.basicConfig(filename=logpath, encoding='utf-8', datefmt='%y-%m-%d %H:%M:%S', format=formatstr, level=numeric_level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.debug(f'program={program}')
    logger.debug(f'time={time}')
    logger.debug(f'logdir={logdir}')
    logger.debug(f'logfile={logfile}')
    logger.info(f'logpath={logpath} loglevel={loglevel}')
    return logger
