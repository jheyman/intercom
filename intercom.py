#!/usr/bin/python
import sys
import traceback
import pjsua as pj
import os
import time
import RPi.GPIO as GPIO
import logging
import logging.handlers
from ConfigParser import SafeConfigParser


PJSIP_LOG_LEVEL=3


###########################
# PERSONAL CONFIG FILE READ
###########################

parser = SafeConfigParser()
parser.read('intercom.ini')

LOG_FILENAME = parser.get('config', 'log_filename')
SIP_SERVER = parser.get('config', 'SIP_server')
SIP_EXTENSION = parser.get('config', 'SIP_extension')
SIP_PASSWORD = parser.get('config', 'SIP_password')
SIP_CALLEXTENSION = parser.get('config', 'SIP_CallExtension')

HAS_PTT = parser.getboolean('config', "HasPushToTalk")

if HAS_PTT:
	PUSHTOTALK_GPIO = parser.getint('config', 'PushToTalkGPIO')

HAS_AUDIOCONTROLLER = parser.getboolean('config', "HasAudioController")


#################
#  LOGGING SETUP
#################
LOG_LEVEL = logging.INFO  # Could be e.g. "DEBUG" or "WARNING"

# Configure logging to log to a file, making a new file at midnight and keeping the last 3 day's data
# Give the logger a unique name (good practice)
logger = logging.getLogger(__name__)
# Set the log level to LOG_LEVEL
logger.setLevel(LOG_LEVEL)
# Make a handler that writes to a file, making a new file at midnight and keeping 3 backups
handler = logging.handlers.TimedRotatingFileHandler(LOG_FILENAME, when="midnight", backupCount=3)
# Format each log message like this
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
# Attach the formatter to the handler
handler.setFormatter(formatter)
# Attach the handler to the logger
logger.addHandler(handler)

# Make a class we can use to capture stdout and sterr in the log
class MyLogger(object):
	def __init__(self, logger, level):
		"""Needs a logger and a logger level."""
		self.logger = logger
		self.level = level

	def write(self, message):
		# Only log if there is a message (not just a new line)
		if message.rstrip() != "":
			self.logger.log(self.level, message.rstrip())

# Replace stdout with logging to file at INFO level
sys.stdout = MyLogger(logger, logging.INFO)
# Replace stderr with logging to file at ERROR level
sys.stderr = MyLogger(logger, logging.ERROR)

logger.info('-------------------------')
logger.info('Starting Intercom Service')
logger.info('Using SIP server: %s' % SIP_SERVER)
logger.info('Using SIP extension: %s' % SIP_EXTENSION)
logger.info('-------------------------')

###########################
#  SIP management functions
###########################

current_call = None

def log_cb(level, str, len):
	logger.info(str)

class MyAccountCallback(pj.AccountCallback):

	def __init__(self, account=None):
		pj.AccountCallback.__init__(self, account)

	# Notification on incoming call
	def on_incoming_call(self, call):
		global current_call 
		if current_call:
			call.answer(486, "Busy")
			return

		logger.info("Incoming call from "+ call.info().remote_uri)

		current_call = call

		call_cb = MyCallCallback(current_call)
		current_call.set_callback(call_cb)

		#current_call.answer(180)
		
		# auto-answer
		current_call.answer(200)

# Callback to receive events from Call
class MyCallCallback(pj.CallCallback):

	def __init__(self, call=None):
		pj.CallCallback.__init__(self, call)

	# Notification when call state has changed
	def on_state(self):
		global current_call
		logger.info("Call with "+ self.call.info().remote_uri +\
		" is "+ self.call.info().state_text +\
		", last code = "+ str(self.call.info().last_code) +\
		" (" + self.call.info().last_reason + ")")

		if self.call.info().state == pj.CallState.CONFIRMED:
			if HAS_AUDIOCONTROLLER:
				# send a (simulated) IR command to the audio controller, so that it can prepare for sound output (mute ongoing music or just turn on amplifier)
				os.system('irsend simulate "0000000000004660 0 KEY_START_ANNOUNCE piremote"')
				os.system('aplay beepbeep.wav')            

		if self.call.info().state == pj.CallState.DISCONNECTED:
			current_call = None
			
			if HAS_AUDIOCONTROLLER:
				# send a (simulated) IR command to the audio controller, so that it can resume its music playback (or just turn off again)
				os.system('irsend simulate "0000000000022136 0 KEY_END_ANNOUNCE piremote"')   

	# Notification when call's media state has changed.
	def on_media_state(self):
		if self.call.info().media_state == pj.MediaState.ACTIVE:
			# Connect the call to sound device
			call_slot = self.call.info().conf_slot
			pj.Lib.instance().conf_connect(call_slot, 0)
			pj.Lib.instance().conf_connect(0, call_slot)
			logger.info("Media is now active")
		else:
			logger.info("Media is inactive")

# Function to make call
def make_call(uri):
	try:
		logger.info("Making call to "+ uri)
		return acc.make_call(uri, cb=MyCallCallback())
	except pj.Error, e:
		logger.error("Exception: " + str(e))
		return None

if HAS_PTT:
	# GPIO setup for push-to-talk button
	GPIO.setmode(GPIO.BCM)
	GPIO.setup(PUSHTOTALK_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Create library instance
lib = pj.Lib()

try:
	my_media_cfg = pj.MediaConfig()
	#my_media_cfg.clock_rate = 8000
	my_media_cfg.no_vad = True
	my_media_cfg.ec_tail_len = 0

#    logcfg =pj.LogConfig()
#    logcfg.level = 0
#    logcfg.callback = log_cb
#    logcfg.msg_logging = True

	lib.init(media_cfg=my_media_cfg)
	#lib.init(media_cfg=my_media_cfg, log_cfg=logcfg)
	#lib.init(log_cfg = pj.LogConfig(level=PJSIP_LOG_LEVEL, callback=log_cb))

	#lib.set_snd_dev(1,1)

	# Create UDP transport which listens to any available port
	transport = lib.create_transport(pj.TransportType.UDP, pj.TransportConfig(0))

	# Start the library
	lib.start()

	acc_cfg = pj.AccountConfig()
	acc_cfg.id = "sip:"+SIP_EXTENSION+ "@"+SIP_SERVER
	acc_cfg.reg_uri = "sip:" + SIP_SERVER
	acc_cfg.proxy = [ "sip:"+SIP_SERVER+";lr" ]
	acc_cfg.auth_cred = [ pj.AuthCred("*", SIP_EXTENSION, SIP_PASSWORD) ]

	acc_cb = MyAccountCallback()
	acc = lib.create_account(acc_cfg, cb=acc_cb)

	cb = MyAccountCallback(acc)
	acc.set_callback(cb)
	logger.info("Registration complete, status="+ str(acc.info().reg_status)+ " (" + acc.info().reg_reason + ")" )

	# PushToTalk button polling loop
	while True:

		if HAS_PTT:
			if GPIO.input(18): 
				if current_call is not None:
					logger.info("Hanging up")
					current_call.hangup()
			else:
				if current_call is None:
					current_call = make_call("sip:"+SIP_CALLEXTENSION+"@" + SIP_SERVER)

		time.sleep(0.2)

except:
	logger.info("*****Exception in main loop ******")
	exc_type, exc_value, exc_traceback = sys.exc_info()
	traceback.print_exception(exc_type, exc_value, exc_traceback,limit=2, file=sys.stdout)	
	del exc_traceback
	# Shutdown the library
	transport = None
	acc.delete()
	acc = None
	lib.destroy()
	lib = None
