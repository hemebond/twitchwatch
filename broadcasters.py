from datetime import datetime, timedelta
import asyncore
import logging
import re

from client import get_current_streams

class IrcBroadcaster(asyncore.dispatcher):
	def __init__(self, network, room, nick, games=[], blacklist=[], port=6667, cmd_limit=30):
		"""
		cmd_limit is the minimum amount of time, in seconds, between IRC command requests
		"""
		import socket

		self.logger = logging.getLogger("IrcBroadcaster (%s:%s)" % (network, port))
		self.logger.debug("__init__()")

		asyncore.dispatcher.__init__(self)

		self._irc_network = network
		self._irc_port = port
		self._irc_room = room
		self._irc_nick = nick
		self._games = games
		self._irc_registered = False
		self._blacklist = blacklist
		self._last_check = None
		self._last_check_limit = cmd_limit

		self.create_socket(socket.AF_INET, socket.SOCK_STREAM)

		try:
			self.connect((network, port))
		except Exception as e:
			self.logger.error("Could not create IrcBroadcaster")
			self.logger.error(e)
			self.close()

	def handle_read(self):
		self.logger.debug("handle_read()")

		buffer = b''
		while True:
			try:
				buffer += self.recv(1024)
			except BlockingIOError as e:
				break

		if buffer:
			# Data is received as bytes, convert to string
			str_data = buffer.decode('UTF-8')

			# Print out the data, commas prevents newline
			self.logger.debug(str_data)

			for line in str_data.split('\r\n'):
				if line.find("End of /MOTD command") != -1:
					self.logger.info("Responding to welcome")
					self._irc_join(self._irc_room)

				elif line.startswith("PING "):
					self.logger.info("Responding to PING")
					self.send('PONG %s\r\n' % line.split()[1])

				elif not self._irc_registered:
					self.logger.info("Sending NICK details")
					self.send("NICK {0}\r\n".format(self._irc_nick))
					self.send("USER {0} {0} {0} :Python IRC\r\n".format(self._irc_nick))
					self._irc_registered = True

				else:
					regex_string = "\:(\S+)\!\S+ PRIVMSG {room} \:{nick}\: ([a-zA-Z0-9'-: ]+)"
					regex = re.compile(regex_string.format(room=self._irc_room, nick=self._irc_nick))
					match = regex.search(line)

					if match:
						user, message = match.groups()
						self.logger.debug("Got message: %s" % message)

						if message == 'quit':
							self.close()
						else:
							# Make sure a certain amount of time has passed since the last command request
							if self._last_check is None or datetime.now() >= (self._last_check + timedelta(seconds=self._last_check_limit)):
								self._last_check = datetime.now()

								# Get the current list of streams
								current_streams = get_current_streams(message)

								for stream in current_streams:
									if stream["channel"]["name"] in self._blacklist:
										self.logger.info("Channel {0} is blacklisted".format(stream["channel"]["name"]))
										current_streams.remove(stream)

								# Construct a message to send to IRC
								stream_urls = [stream["channel"]["url"] for stream in current_streams]

								if stream_urls:
									msg = "{user}: Current {game} streams include {streams}".format(user=user,
									                                                                game=message,
									                                                                streams=", ".join(stream_urls))
								else:
									msg = "{user}: There are no {game} streams.".format(user=user, game=message)

								self._irc_send(msg)
							else:
								# Not enough time has passed since the last request
								self.logger.info("Not enough time since last command request")

	def send(self, msg):
		self.logger.debug("send()")
		msg = bytes(msg, "UTF-8")
		super().send(msg)

	def _irc_send(self, msg):
		self.logger.debug("_irc_send()")
		self.send("PRIVMSG %s : %s\r\n" % (self._irc_room, msg))

	def _irc_join(self, chan):
		self.logger.debug("_irc_join()")
		self.send("JOIN %s\r\n" % chan)

		msg = ''
		while msg.find('End of /NAMES list.') == -1:
			try:
				msg = self.recv(2048).decode('UTF-8')
			except BlockingIOError as e:
				break

			msg = msg.strip('\r\n')
			self.logger.info(msg)

	def broadcast(self, stream):
		self.logger.debug("broadcast()")

		# Only send the notification if the game list is empty
		# or if the game is in the list
		if self._games == [] or stream["game"] in self._games:
			self._irc_send("{game} | {status} | {url}".format(game=stream['game'],
			                                                  url=stream['channel']['url'],
			                                                  status=stream['channel']['status'].replace('\n', ' ')))


class DbusBroadcaster(object):
	def __init__(self, **kwargs):
		import dbus

		self.logger = logging.getLogger("DbusBroadcaster")
		self.logger.debug("__init__()")

		_bus_name = "org.freedesktop.Notifications"
		_object_path = "/org/freedesktop/Notifications"
		_interface_name = _bus_name
		session_bus = dbus.SessionBus()
		obj = session_bus.get_object(_bus_name, _object_path)
		self._interface = dbus.Interface(obj, _interface_name)

	def broadcast(self, stream):
		self.logger.debug("broadcast()")

		msg_summary = "New \"{0}\" stream".format(stream['game'])
		msg_body = "{0}".format(stream['channel']['url'])
		self._interface.Notify("TwitchWatch", 0, "", msg_summary, msg_body, [], {}, -1)
