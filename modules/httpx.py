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

HTTP_RESPONSE_STATUS_LINE = 4
HTTP_RESPONSE_HEADER = 5
HTTP_RESPONSE_BODY = 6

class HTTPX(object):
	def __init__(self):
		self._state = HTTP_INIT
		self._headers = []
		self._host = ''
		self._start = 0
		self._pos = 0
		self._pending_data = ''

	def check_header(self):
		if self._state >= HTTP_REQUEST_HEADER:
			return True
		else:
			return False 

	def get_data(self):
		#logging.info('get_data')
		url = ''
		if len(self._host) > 0 and self._request_line.find(self._host) != -1:
			url = self._request_line.replace(self._host, '/', 1)
		

		

		if url.find('http://') != -1:
			logging.info('request_line:%s', self._request_line)
			logging.info('host:%s', self._host)
			logging.info('url:%s', url)

		data = url + '\r\n'
		for item in self._headers:
			data += (item + '\r\n')
		data += '\r\n'
		data += self._pending_data

		logging.info('after:%s, %d, %d', data, len(self._host), self._request_line.find(self._host))

		self._request_line = ''
		self._headers = []
		self._pending_data = ''
		return data

	def get_response(self):
		data = self._response_status_line + '\r\n'
		for item in self._headers:
			data += (item + '\r\n')
		data += '\r\n'
		data += self._pending_data

		self._response_status_line = ''
		self._headers = []
		self._pending_data = ''
		return data



	def get_host_address_and_port(self):
		#logging.info('get_host_address_and_port')
		host = ''
		addr = ''
		port = 80
		self._host = ''
		for item in self._headers:
			
			if item.find('Host:') != -1:
				logging.debug(item)
				host = item[6:]
				self._host = 'http://' + host + '/'
				break
		if len(self._host) == 0:
			start = self._request_line.find('//')
			end = self.request_line.find('/', start + 2)
			host = self._request_line[start + 2 : end]
			self._host = 'http://' + self._request_line[start + 2 : end] + '/'
			logging.debug('_host:%s', self._host)
			self._headers.append('Host: ' + host)
			
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
				start += 2
				break
			#logging.debug('header:' + buf[start:index])
			self._headers.append(buf[start:index])
			start = index + 2
			index = buf.find('\r\n', start)
			#logging.debug('header:start:%d, index:%d', start, index)
			#logging.debug('header:' + buf[start:])
		self._pending_data = buf[start:]
		#logging.debug('state:%d', self._state)

	def _parse_response_headers(self, buf, start):
		index  = buf.find('\r\n', start)
		while index != -1:
			if index == start:
				self._state = HTTP_RESPONSE_HEADER
				start += 2
				break
			#logging.debug('header:' + buf[start:index])
			self._headers.append(buf[start:index])
			start = index + 2
			index = buf.find('\r\n', start)
			#logging.debug('header:start:%d, index:%d', start, index)
			#logging.debug('header:%s' ,buf[start:index])
		self._pending_data = buf[start:]
		#logging.debug('state:%d', self._state)


	# save data in pending buf
	def _parse_body(self, buf, start):
		self._pending_data = buf

	def _parse_response_body(self, buf, start):
		self._pending_data = buf

	def parse_request(self, buf):
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
			if data.find('GET') == -1 and data.find('POST') == -1:
				return False



			index = data.find('\r\n')
			
			#logging.info('origin:%s', data[0:index])
			if index != -1:
				self._request_line = data[0:index]
				#logging.info('request:%s', self._request_line)
				self._state = HTTP_REQUEST_LINE
				self._parse_headers(data, index + 2)
			else:
				self._pending_data = data
			return True

		# already get the reqeust line
		if self._state == HTTP_REQUEST_LINE:
			self._parse_headers(data, 0)
			return True

		# already get all headers
		if self._state == HTTP_REQUEST_HEADER:
			self._parse_body(data, 0)
			return True

	def parse_response(self, buf):
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
				self._response_status_line = data[0:index]
				self._state = HTTP_RESPONSE_STATUS_LINE
				self._parse_response_headers(data, index + 2)
			else:
				self._pending_data = data
			return

		# already get the reqeust line
		if self._state == HTTP_RESPONSE_LINE:
			self._parse_response_headers(data, 0)

		# already get all headers
		if self._state == HTTP_RESPONSE_HEADER:
			self._parse_response_body(data, 0)










