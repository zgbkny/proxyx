import os
import json
import sys
import getopt
import logging

VERBOSE_LEVEL = 5

def get_config():
	logging.basicConfig(level=logging.DEBUG,
						format='%(levelname)-s: %(message)s')
	config = {}
	config['workers'] = 1;
	config['server_address'] = '0.0.0.0'
	config['server_port'] = 8388
	config['timeout'] = 300
	config['verbose'] = False
	config['fast_open'] = False

	return config