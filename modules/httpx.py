from __future__ import absolute_import, division, print_function, with_statement

import sys
import os

import time
import socket
import errno
import struct
import logging
import traceback
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

HTTP_INIT = 0
HTTP_REQUEST_LINE = 1
HTTP_REQUEST_HEADER = 2
HTTP_REQUEST_BODY = 3

class HTTPX(object):
	def __init__(self):
		self._state = HTTP_INIT
		self._headers = []
		self._start = 0
		self._pos = 0
		self._pending_data = ''

	def check_header(self):
		if self._state >= HTTP_REQUEST_HEADER:
			return True
		else:
			return False 

	def get_host_address_and_port(self):
		logging.debug('get_host_address_and_port')
		host = ''
		addr = ''
		port = 80
		for item in self._headers:
			
			if item.find('Host:') != -1:
				logging.debug(item)
				host = item[6:]
				break
		logging.debug(host)
		if host.find(':') != -1:
			addr = host[0:host.find(':')]
			port = int(host[host.find(':') + 1:])
		else:
			addr = host
		logging.debug('%s, %d', addr, port)
		return addr, port

	def _parse_headers(self, buf, start):
		index  = buf.find('\r\n', start)
		while index != -1:
			if index == start:
				self._state = HTTP_REQUEST_HEADER
			#logging.debug('header:' + buf[start:index])
			self._headers.append(buf[start:index])
			start = index + 2
			index = buf.find('\r\n', start)
			#logging.debug('header:start:%d, index:%d', start, index)
			#logging.debug('header:' + buf[start:])
		self._pending_data = buf[start:]
		#logging.debug('state:%d', self._state)
	# save data in pending buf
	def _parse_body(self, buf, start):
		self._pending_data = buf

	def parse(self, buf):
		#logging.debug('HTTPX:parse')
		index = -1
		data = ''
		if len(self._pending_data) != 0:
			data = self._pending_data + buf
			self._pending_data = ''
		else:
			data = buf

		# not get request line yet
		if self._state == HTTP_INIT:
			#logging.debug('request_line:' + data[0:data.find('\r\n')])
			index = data.find('\r\n')
			if index != -1:
				self._request_line = data[0:index]
				self._state = HTTP_REQUEST_LINE
				self._parse_headers(data, index + 2)
			else:
				self._pending_data = data
			return

		# already get the reqeust line
		if self._state == HTTP_REQUEST_LINE:
			self._parse_headers(data, 0)

		# already get all headers
		if self._state == HTTP_REQUEST_HEADER:
			self._parse_body(data, 0)










