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
        self.auth_services = auth_services
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
        except (ConnectionError, asyncio.CancelledError) as e:
            self.logger.error(f"Send failed: {e}")
            await self.disconnect()

    async def read_line_with_timeout(self, timeout=15):
        try:
            line_bytes = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
            if not line_bytes:
                self.logger.warning("Connection closed by server.")
                return None
            return line_bytes.decode("utf-8", errors="ignore").strip()
        except asyncio.TimeoutError:
            self.logger.warning("⏱ Timeout waiting for server line.")
            return None

    async def connect(self):
        self.logger.info(f"Connecting to {self.server}:{self.port}...")
        try:
            if self.port == 6697:
                context = ssl.create_default_context(cafile=certifi.where())
                self.reader, self.writer = await asyncio.open_connection(self.server, self.port, ssl=context)
            else:
                self.reader, self.writer = await asyncio.open_connection(self.server, self.port)
            self.connected = True
            self.logger.info("Connection successful.")
            return True
        except ssl.SSLCertVerificationError as e:
            self.logger.error(f"❌ SSL Certificate error: {e}")
        except ConnectionError as e:
            self.logger.error(f"❌ Connection failed: {e}")
        except socket.gaierror as e:
            self.logger.error(f"❌ DNS lookup failed: {self.server}. Error: {e}")
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

    def generate_unique_nickname(self):
        suffix = random.randint(100, 999)
        self.nickname = f"{self.original_nickname}_{suffix}"
        self.logger.info(f"Nickname in use, trying new: {self.nickname}")

    async def handle_authentication(self):
        try:
            # Initial CAP LS request with longer timeout
            await self.send("CAP LS 302")

            sasl_supported = False
            cap_ls_timeout = 30
            start_time = time.time()

            while True:
                remaining = cap_ls_timeout - (time.time() - start_time)
                if remaining <= 0:
                    self.logger.warning("Timeout waiting for CAP LS response.")
                    break

                line = await self.read_line_with_timeout(timeout=remaining)
                if not line:
                    continue
                self.logger.debug(f"<< {line}")

                if "CAP" in line and "LS" in line:
                    parts = line.split(":", 2)
                    if len(parts) > 2:
                        cap_str = parts[-1].lower()
                        if "sasl" in cap_str:
                            sasl_supported = True
                            self.logger.info("Server supports SASL (from CAP LS).")
                    break

            self.sasl_supported = sasl_supported

            if sasl_supported:
                await self.send("CAP REQ :sasl")
                # Wait for ACK/NAK for SASL request
                while True:
                    line = await self.read_line_with_timeout(timeout=10)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    if "CAP" in line and "ACK" in line and "sasl" in line.lower():
                        self.sasl_supported = True
                        self.logger.info("SASL capability acknowledged by server.")
                        await self.send("AUTHENTICATE PLAIN")
                        break
                    if "CAP" in line and "NAK" in line and "sasl" in line.lower():
                        self.logger.warning("SASL not supported by this server.")
                        self.sasl_supported = False
                        break

            await self.send(f"NICK {self.nickname}")
            await self.send(f"USER {self.nickname} 0 * :{self.nickname}")

            # Registration/authentication loop
            start_time = time.time()
            timeout = 30

            while True:
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    self.logger.error("Timeout during registration/auth.")
                    return False

                line = await self.read_line_with_timeout(timeout=remaining)
                if not line:
                    continue

                self.logger.debug(f"<< {line}")
                start_time = time.time()  # Reset timeout on any activity

                # Handle late CAP LS line (catch SASL support if missed)
                if "CAP" in line and "LS" in line:
                    parts = line.split(":", 2)
                    if len(parts) > 2:
                        cap_str = parts[-1].lower()
                        if "sasl" in cap_str and not self.sasl_supported:
                            self.logger.info("Detected SASL support late during registration. Requesting SASL now.")
                            await self.send("CAP REQ :sasl")
                            self.sasl_supported = True
                    continue

                if line.startswith("PING"):
                    await self.send(f"PONG :{line.split(':', 1)[1]}")
                    continue

                if " 433 " in line:
                    self.logger.warning(f"Nickname '{self.nickname}' in use.")
                    self.generate_unique_nickname()
                    await self.send(f"NICK {self.nickname}")
                    continue

                if "AUTHENTICATE +" in line and self.sasl_supported:
                    auth_str = base64.b64encode(f"\0{self.nickname}\0{self.password}".encode()).decode()
                    self.logger.info("Sending SASL AUTHENTICATE payload.")
                    await self.send(f"AUTHENTICATE {auth_str}")
                    continue

                if "903" in line:
                    self.logger.info("✅ SASL authentication successful.")
                    await self.send("CAP END")
                    self.authenticated = True
                    return True

                if "904" in line or "905" in line:
                    self.logger.warning("❌ SASL authentication failed.")
                    break

                if " 001 " in line:
                    self.logger.info("✅ Registered (001 welcome).")
                    break

            # Fallback: try NickServ/X/Q identify (rest of your logic unchanged)
            if self.auth_services:
                service = self.auth_services[-1].lower()

                identify_cmd = None
                register_cmd = None
                if service == "nickserv":
                    identify_cmd = f"PRIVMSG NickServ :IDENTIFY {self.password}"
                    register_cmd = f"PRIVMSG NickServ :REGISTER {self.password} {self.email}"
                elif service == "x":
                    identify_cmd = f"PRIVMSG X@channels.undernet.org :LOGIN {self.nickname} {self.password}"
                    register_cmd = f"PRIVMSG X@channels.undernet.org :REGISTER {self.password} {self.email}"
                elif service == "q":
                    identify_cmd = f"PRIVMSG Q@CServe.quakenet.org :AUTH {self.nickname} {self.password}"
                    register_cmd = f"PRIVMSG Q@CServe.quakenet.org :REGISTER {self.email} {self.password}"
                else:
                    self.logger.warning(f"Unsupported auth service: {service}")
                    return False

                await self.send(identify_cmd, logging.INFO)
                await asyncio.sleep(5)

                auth_success = False
                line = await self.read_line_with_timeout(timeout=10)

                while not line:
                    line = await self.read_line_with_timeout(timeout=10)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    if any(word in line.lower() for word in ["identified", "logged in", "accepted", "successfully"]):
                        auth_success = True
                        break
                    if any(kw in line.lower() for kw in ["invalid password", "incorrect", "authentication failed", "not recognized"]):
                        break

                if auth_success:
                    self.logger.info("✅ IDENTIFY succeeded.")
                    await self.send("CAP END")
                    self.authenticated = True
                    return True

                self.logger.warning("❌ IDENTIFY failed. Trying REGISTER...")

                await self.send(register_cmd, logging.INFO)
                await asyncio.sleep(10)

                self.logger.info("🔁 Retrying IDENTIFY after REGISTER...")
                await self.send(identify_cmd, logging.INFO)
                await asyncio.sleep(5)

                for _ in range(5):
                    line = await self.read_line_with_timeout(timeout=10)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    if any(word in line.lower() for word in ["identified", "logged in", "accepted", "successfully"]):
                        self.logger.info("✅ IDENTIFY successful after REGISTER.")
                        await self.send("CAP END")
                        self.authenticated = True
                        return True

                self.logger.error("❌ REGISTER + IDENTIFY failed.")
                return False

            await self.send("CAP END")
            self.authenticated = True
            return True

        except Exception:
            self.logger.error("Exception during authentication:")
            self.logger.error(traceback.format_exc())
            return False


    async def run(self):
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

            await self.send(f"JOIN {self.channel_name}")
            self.joined_channel = True

            try:
                while self.connected:
                    line = await self.read_line_with_timeout(timeout=60)
                    if not line:
                        continue

                    self.logger.debug(f"<< {line}")

                    if line.startswith("PING"):
                        await self.send(f"PONG :{line.split(':', 1)[1]}")

                    elif f"JOIN :{self.channel_name}".lower() in line.lower() and self.nickname.lower() in line.lower():
                        self.logger.info(f"Joined channel {self.channel_name}")

                    elif f"PRIVMSG {self.nickname}" in line:
                        prefix = line.split(" ")[0]
                        sender_nick = prefix[1:].split("!")[0]
                        msg = line.split(":", 2)[2].strip()

                        if msg.startswith("\x01") and msg.endswith("\x01"):
                            ctcp_command = msg[1:-1].upper()
                            self.logger.info(f"📡 Received CTCP {ctcp_command} from {sender_nick}")
                            if ctcp_command == "VERSION":
                                await self.send(f"NOTICE {sender_nick} :\x01VERSION TurboBot 1.0 (Python)\x01")
                        elif msg.upper() == "VERSION":
                            self.logger.info(f"📡 Received plain VERSION request from {sender_nick}")
                            await self.send(f"PRIVMSG {sender_nick} :VERSION TurboBot 1.0 (Python)")
                        else:
                            await self.send(f"PRIVMSG {sender_nick} :echo")

            except (asyncio.TimeoutError, ConnectionError) as e:
                self.logger.error(f"Network error: {e}")
                await self.disconnect()
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}")
                await self.disconnect()

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

async def main():
    nickname = "turtoise"
    password = "seawater"
    email = "turtoise@gmail.com"
    channel_name = "#echochannel"

    irc_networks = {
        "Libera.Chat":      [("irc.libera.chat", 6697), "SASL", "NickServ"],
        "OFTC":             [("irc.oftc.net", 6697), "SASL", "NickServ"],
        "Undernet":         [("irc.undernet.org", 6697), "X"],
        "QuakeNet":         [("irc.quakenet.org", 6697), "Q"],
        "EFnet":            [("irc.efnet.org", 6697), "SASL", "NickServ"],
        "DALnet":           [("irc.dal.net", 6697), "SASL", "NickServ"],
        "EsperNet":         [("irc.esper.net", 6697), "SASL", "NickServ"],
        "GeekShed":         [("irc.geekshed.net", 6697), "SASL", "NickServ"],
    }

    clients = []
    tasks = []

    for net_name, (server_info, *auth_services) in irc_networks.items():
        client = IRCClient(net_name, server_info, auth_services, nickname, password, email, channel_name)
        clients.append(client)
        tasks.append(asyncio.create_task(client.run()))

    # Add the status reporter task
    tasks.append(asyncio.create_task(status_reporter(clients, 90)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass  # Expected when a task is cancelled
    except KeyboardInterrupt:
        print("Keyboard interrupt received. Exiting gracefully...")
        # Cancel all tasks and wait for them to finish their cleanup
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program shut down.")
