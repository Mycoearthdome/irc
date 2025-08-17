import asyncio
import ssl
import base64
import logging
import certifi
import random
import time
import traceback
import socket

logging.basicConfig(
    format='[%(asctime)s] %(levelname)s:%(name)s: %(message)s',
    datefmt='%H:%M:%S',
    level=logging.DEBUG
)

class IRCClient:
    def __init__(self, net_name, server_info, auth_services, nickname, password, email, channel_name):
        self.net_name = net_name
        self.server = server_info[0]
        self.port = server_info[1]
        # Fix: ensure auth_services is always a list
        if isinstance(auth_services, str):
            self.auth_services = [auth_services]
        elif isinstance(auth_services, (list, tuple)):
            self.auth_services = list(auth_services)
        else:
            self.auth_services = []

        self.nickname = nickname
        self.original_nickname = nickname
        self.password = password
        self.email = email
        self.channel_name = channel_name
        self.reader = None
        self.writer = None
        self.logger = logging.getLogger(f"IRCClient-{self.net_name}")
        self.authenticated = False
        self.connected = False
        self.joined_channel = False
        self.reconnect_delay = 10
        self.sasl_supported = False

    async def send(self, command, log_level=logging.DEBUG):
        if not self.writer:
            self.logger.warning(f"Attempted to send while disconnected: {command}")
            return
        try:
            self.writer.write(command.encode("utf-8") + b"\r\n")
            await self.writer.drain()
            self.logger.log(log_level, f">> {command}")
        except (ConnectionError, asyncio.CancelledError, AttributeError) as e:
            self.logger.error(f"Send failed (connection lost): {e}")
            self.connected = False
            self.writer = None
            self.reader = None
        

    async def read_line_with_timeout(self, timeout=15):
        if not self.reader:
            return None
        try:
            line_bytes = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
            if not line_bytes:
                return None
            return line_bytes.decode("utf-8", errors="ignore").strip()
        except asyncio.TimeoutError:
            return None
        except (ConnectionError, asyncio.IncompleteReadError) as e:
            self.logger.error(f"❌ Connection error while reading: {e}")
            return None

    async def connect(self):
        self.logger.info(f"Connecting to {self.server}:{self.port}...")
        try:
            if self.port == 6697:
                context = ssl.create_default_context(cafile=certifi.where())
                self.reader, self.writer = await asyncio.open_connection(self.server, self.port, ssl=context)
            else:
                self.reader, self.writer = await asyncio.open_connection(self.server, self.port)
            if self.reader and self.writer:
                self.connected = True
                self.logger.info("Connection successful.")
                self.logger.debug(f"Reader: {self.reader}, Writer: {self.writer}")
                return True
            else:
                self.logger.error("❌ Failed to establish reader/writer streams.")
                self.connected = False
                return False
        except ssl.SSLCertVerificationError as e:
            self.logger.error(f"❌ SSL Certificate error: {e}")
        except ConnectionError as e:
            self.logger.error(f"❌ Connection failed: {e}")
        except socket.gaierror as e:
            self.logger.error(f"❌ DNS lookup failed: {self.server}. Error: {e}")
        except OSError as e:
            self.logger.error(f"❌ OS error: {self.server}. Error: {e}")
        self.connected = False
        return False

    async def disconnect(self):
        if self.writer:
            try:
                await self.send("QUIT :Leaving", logging.INFO)
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.connected = False
        self.authenticated = False
        self.joined_channel = False
        self.logger.info("Disconnected.")

    async def handle_message_loop(self):
        """Main loop to handle incoming IRC messages after authentication."""
        try:
            while self.connected:
                line = await self.read_line_with_timeout(timeout=300)
                if not line:
                    continue

                self.logger.debug(f"<< {line}")

                # Respond to server PINGs to stay connected
                if line.startswith("PING"):
                    pong_response = line.replace("PING", "PONG", 1)
                    await self.send(pong_response, logging.DEBUG)
                    continue

                # Handle a PRIVMSG (someone sending a message to a channel or PM to the bot)
                if "PRIVMSG" in line:
                    prefix, trailing = line.split(" :", 1)
                    parts = prefix.split()
                    if len(parts) >= 3:
                        sender = parts[0][1:].split('!')[0]
                        target = parts[2]  # could be a channel or our nick
                        message = trailing

                        self.logger.info(f"[{target}] <{sender}> {message}")

                        # Simple echo bot: reply back if the message is in a channel
                        if target.startswith("#"):
                            reply = f"PRIVMSG {target} :You said: {message}"
                            await self.send(reply, logging.DEBUG)
        except Exception as e:
            self.logger.error(f"💥 Exception in message loop: {e}")
            self.connected = False

    async def is_nickname_registered(self):
        # Send INFO command and parse replies to detect if nick is registered and verified
        await self.send(f"PRIVMSG NickServ :INFO {self.nickname}")

        verified = False
        registered = False
        timeout = 15
        start = time.time()

        while time.time() - start < timeout:
            line = await self.read_line_with_timeout(timeout=timeout - (time.time() - start))
            if not line:
                if registered and verified:
                    return True  # registered and verified means SASL can be attempted
                elif registered and not verified:
                    # Nick is registered but not verified — no SASL
                    return False
                continue

            if "VERIFIED: YES" in line.upper():
                verified = True
            if "is already registered" in line.lower():
                registered = True

        # Default fallback if no info
        return False

    async def handle_authentication(self):
        if not self.connected or not self.reader or not self.writer:
            self.logger.error("❌ Cannot authenticate: not connected to server.")
            return False
        
        try:
            await self.send(f"NICK {self.nickname}")
            await self.send(f"USER {self.nickname} 0 * :{self.nickname}")

            # Check if nickname is registered
            is_registered = False
            if self.auth_services and "nickserv" in [s.lower() for s in self.auth_services]:
                is_registered = await self.is_nickname_registered()
                if not is_registered:
                    self.logger.warning(f"📛 Nickname '{self.nickname}' is not registered. Attempting to REGISTER...")
                    service = self.auth_services[-1].lower()
                    if service == "nickserv":
                        register_cmd = f"PRIVMSG NickServ :REGISTER {self.password} {self.email}"
                    elif service == "x":
                        register_cmd = f"PRIVMSG X@channels.undernet.org :REGISTER {self.password} {self.email}"
                    elif service == "q":
                        register_cmd = f"PRIVMSG Q@CServe.quakenet.org :REGISTER {self.email} {self.password}"
                    elif service == "dalnet":
                        register_cmd = f"PRIVMSG NickServ@services.dal.net :REGISTER {self.password} {self.email}"
                    else:
                        register_cmd = None

                    if register_cmd:
                        await self.send(register_cmd, logging.INFO)
                        await asyncio.sleep(10)
                else:
                    self.logger.info(f"✅ Nickname '{self.nickname}' is already registered.")

            # ---- CAP LS ----
            await self.send("CAP LS 302")
            cap_lines = []
            cap_timeout = 15
            cap_start = time.time()

            while time.time() - cap_start < cap_timeout:
                line = await self.read_line_with_timeout(timeout=cap_timeout - (time.time() - cap_start))
                if not line:
                    continue
                self.logger.debug(f"<< {line}")
                if "CAP" in line and " LS " in line:
                    cap_lines.append(line)
                    if "CAP * LS" not in line:
                        break  # last line

                # Respond to server PINGs to stay connected
                if line.startswith("PING"):
                    pong_response = line.replace("PING", "PONG", 1)
                    await self.send(pong_response, logging.DEBUG)
                    continue

                if " 001 " in line:
                    self.logger.info("✅ Logged in (001 welcome).")
                    self.authenticated = True
                    break

            cap_str = " ".join(cap_lines).lower()
            self.sasl_supported = "sasl" in cap_str

            if self.sasl_supported and is_registered:
                self.logger.info("✅ Server supports SASL. Requesting...")
                await self.send("CAP REQ :sasl")

                # Wait for ACK or NAK
                ack_timeout = 10
                ack_start = time.time()
                while time.time() - ack_start < ack_timeout:
                    line = await self.read_line_with_timeout(timeout=ack_timeout - (time.time() - ack_start))
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    if "CAP" in line and "ACK" in line and "sasl" in line.lower():
                        await self.send("AUTHENTICATE PLAIN")
                        break
                    elif "CAP" in line and "NAK" in line and "sasl" in line.lower():
                        self.logger.warning("❌ Server NAK'd SASL request.")
                        self.sasl_supported = False
                        break

            # ---- SASL Flow ----
            if self.sasl_supported:
                sasl_start = time.time()
                while time.time() - sasl_start < 30:
                    line = await self.read_line_with_timeout(timeout=5)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")

                    if line.startswith("PING"):
                        await self.send(f"PONG :{line.split(':', 1)[1]}")
                        continue

                    if "AUTHENTICATE +" in line:
                        auth_str = base64.b64encode(f"\0{self.nickname}\0{self.password}".encode()).decode()
                        await self.send(f"AUTHENTICATE {auth_str}")
                        continue

                    if any(code in line for code in ["903", "900"]):  # success
                        self.logger.info("✅ SASL authentication successful.")
                        self.authenticated = True
                        break

                    if any(code in line for code in ["904", "905", "906", "907"]):
                        self.logger.warning(f"❌ SASL authentication failed. Line: {line}")
                        break

                    if " 001 " in line:
                        self.logger.info("✅ Logged in (001 welcome).")
                        self.authenticated = True
                        break

            # ---- IDENTIFY Fallback ----
            if not self.authenticated and "nickserv" in [s.lower() for s in self.auth_services]:
                identify_cmd = f"PRIVMSG NickServ :IDENTIFY {self.password}"
                await self.send(identify_cmd, logging.INFO)
                await asyncio.sleep(5)
                for _ in range(5):
                    line = await self.read_line_with_timeout(timeout=10)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    lowered = line.lower()
                    if any(k in lowered for k in ["identified", "already logged in", "successfully", "accepted"]):
                        self.logger.info("✅ IDENTIFY succeeded.")
                        self.authenticated = True
                        break
                    elif any(k in lowered for k in ["invalid password", "wrong password", "password incorrect"]):
                        self.logger.warning("❌ IDENTIFY failed.")
                        break

            if not self.authenticated and "dalnet" in [s.lower() for s in self.auth_services]:
                identify_cmd = f"PRIVMSG NickServ@services.dal.net :IDENTIFY {self.password}"
                await self.send(identify_cmd, logging.INFO)
                await asyncio.sleep(5)
                for _ in range(5):
                    line = await self.read_line_with_timeout(timeout=10)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    lowered = line.lower()
                    if any(k in lowered for k in ["identified", "already logged in", "successfully", "accepted"]):
                        self.logger.info("✅ IDENTIFY succeeded.")
                        self.authenticated = True
                        break
                    elif any(k in lowered for k in ["invalid password", "wrong password", "password incorrect"]):
                        self.logger.warning("❌ IDENTIFY failed.")
                        break

            # ---- Final Wait for 001 if Needed ----
            if not self.authenticated:
                login_timeout = 30
                login_start = time.time()
                while time.time() - login_start < login_timeout:
                    line = await self.read_line_with_timeout(timeout=login_timeout - (time.time() - login_start))
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    if " 001 " in line:
                        self.logger.info("✅ Logged in (001 welcome).")
                        self.authenticated = True
                        break

            # Always CAP END last
            if self.connected and not self.writer.is_closing():
                await self.send("CAP END")

            return self.authenticated

        except Exception as e:
            self.logger.exception(f"Exception during authentication: {e}")
            return False


    async def run(self):
        """The main execution method for the IRC client."""
        try:
            while True:
                self.connected = False
                self.authenticated = False
                self.joined_channel = False

                if not await self.connect():
                    self.logger.error(f"Connection failed. Retrying in {self.reconnect_delay}s.")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 2, 300)
                    continue

                self.reconnect_delay = 10

                if not await self.handle_authentication():
                    self.logger.warning("Failed to authenticate. Disconnecting.")
                    await self.disconnect()
                    await asyncio.sleep(self.reconnect_delay)
                    continue

                if self.authenticated:
                    await self.send(f"JOIN {self.channel_name}", logging.INFO)
                    self.joined_channel = True
                    await self.handle_message_loop()

                await self.disconnect()
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 300)
        except Exception as e:
            self.logger.exception(f"❌ Unhandled exception in run(): {e}")

async def status_reporter(clients, interval):
    """Periodically reports the status of all IRC clients."""
    while True:
        await asyncio.sleep(interval)
        print("\n" + "="*65)
        print("📈 IRC Server Status Report")
        print("="*65)
        for client in clients:
            status = "✅ Connected" if client.connected else "❌ Disconnected"
            auth_status = "✅ Authenticated" if client.authenticated else "❌ Not Authenticated"
            print(f"| {client.net_name:<15} | {status:<18} | {auth_status:<20} |")
        print("="*65 + "\n")

async def run_client_with_error_handling(client):
    try:
        await client.run()
    except Exception as e:
        client.logger.exception(f"❌ Client crashed: {e}")

async def shutdown(clients, tasks):
    for client in clients:
        await client.disconnect()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

async def main():
    nickname = "doggybag2025"
    password = "takeout"
    email = "doggybag2025@gmail.com"
    channel_name = "#echochannel"

    irc_networks = {
        # Common major networks
        "Libera.Chat":      [("irc.libera.chat", 6697), "SASL", "NickServ"],
        "OFTC":             [("irc.oftc.net", 6697), "NickServ"],
        "Undernet":         [("irc.undernet.org", 6667), "X"],
        #"EFnet":            [("irc.efnet.org", 6697), "NickServ"],
        "DALnet":           [("irc.dal.net", 6697), "DALnet"],
        #"Rizon":            [("irc.rizon.net", 6697), "NickServ"],
        "QuakeNet":         [("irc.quakenet.org", 6667), "Q"],
        #"Snoonet":          [("irc.snoonet.org", 6697), "NickServ"],

        # Other well-known networks
        #"FreenodeClassic":  [("irc.freenode.net", 6697), "NickServ"],
        #"IRCNet":           [("irc.ircnet.net", 6667), None],
        "GameSurge":        [("irc.gamesurge.net", 6667), "AuthServ"],
        #"EsperNet":         [("irc.esper.net", 6697), "SASL", "NickServ"],
        #"Hackint":          [("irc.hackint.org", 6697), "NickServ"],
        #"GeekShed":         [("irc.geekshed.net", 6697), "NickServ"],
        #"SpigotMC":         [("irc.spi.gt", 6697), "NickServ"],
        #"PIRC":             [("irc.pirc.pl", 6697), "NickServ"],
        #"DarkMyst":         [("irc.darkmyst.org", 6697), "SASL", "NickServ"],
        #"SlashNET":         [("irc.slashnet.org", 6667), "NickServ"],
        #"DALnet-EU":        [("irc.eu.dal.net", 6697), "NickServ"],
        #"EFnet-US":         [("irc.efnet.org", 6697), "NickServ"],
    }
    
    clients = []
    tasks = []

    # Add the status reporter task
    for net_name, (server_info, *auth_services) in irc_networks.items():
        client = IRCClient(net_name, server_info, auth_services, nickname, password, email, channel_name)
        clients.append(client)
        tasks.append(asyncio.create_task(run_client_with_error_handling(client)))

    tasks.append(asyncio.create_task(status_reporter(clients, 90)))
    
    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n💥 KeyboardInterrupt or Cancelled. Shutting down...")
        await shutdown(clients, tasks)
    except Exception:
        print("Unexpected exception:")
        traceback.print_exc()
        await shutdown(clients, tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program shut down.")
