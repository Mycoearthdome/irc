import socket
import ssl
import time
import threading
import base64

# Configuration
nickname = "turtoise"
password = "seawater"
email = "turtoise@gmail.com"
channel_name = "#echochannel"

irc_networks = {
    # Common major networks
    "Libera.Chat":      [("irc.libera.chat", 6697), "SASL", "NickServ"],
    "OFTC":             [("irc.oftc.net", 6697), "SASL", "NickServ"],
    "Undernet":         [("irc.undernet.org", 6667), "X"],
    "EFnet":            [("irc.efnet.org", 6697), "NickServ"],
    "DALnet":           [("irc.dal.net", 6697), "NickServ"],
    "Rizon":            [("irc.rizon.net", 6697), "NickServ"],
    "QuakeNet":         [("irc.quakenet.org", 6667), "Q"],
    "Snoonet":          [("irc.snoonet.org", 6697), "NickServ"],

    # Other well-known networks
    "FreenodeClassic":  [("irc.freenode.net", 6697), "NickServ"],
    "IRCNet":           [("irc.ircnet.net", 6667), None],
    "GameSurge":        [("irc.gamesurge.net", 6667), "AuthServ"],
    "EsperNet":         [("irc.esper.net", 6697), "NickServ"],
    "Hackint":          [("irc.hackint.org", 6697), "NickServ"],
    "GeekShed":         [("irc.geekshed.net", 6697), "NickServ"],
    "SpigotMC":         [("irc.spi.gt", 6697), "NickServ"],
    "PIRC":             [("irc.pirc.pl", 6697), "NickServ"],
    "DarkMyst":         [("irc.darkmyst.org", 6697), "NickServ"],
    "SlashNET":         [("irc.slashnet.org", 6667), "NickServ"],
    "DALnet-EU":        [("irc.eu.dal.net", 6697), "NickServ"],
    "EFnet-US":         [("irc.efnet.org", 6697), "NickServ"],

    # Smaller or niche networks
    "Rezosup":          [("irc.rezosup.org", 6697), "NickServ"],
    "TilaaNet":         [("irc.tilaa.net", 6697), "NickServ"],
    "Xertion":          [("irc.xertion.org", 6697), "NickServ"],
    "420chan":          [("irc.420chan.org", 6697), "NickServ"],

    # Additional known IRC networks
    "AnimeNET":         [("irc.animenetwork.org", 6697), "NickServ"],
    "Blizzard":         [("irc.blizzardirc.net", 6697), "NickServ"],
    "BattleNet":        [("irc.battle.net", 6667), "NickServ"],
    "Haskell":          [("irc.haskell.org", 6667), "NickServ"],
    "Libera":           [("irc.libera.chat", 6667), "SASL", "NickServ"],
    "MetalIRC":         [("irc.metalirc.org", 6697), "NickServ"],
    "MozNet":           [("irc.moznet.org", 6667), "NickServ"],
    "NetRevolution":    [("irc.netrev.net", 6697), "NickServ"],
    "Netsplit.de":      [("irc.netsplit.de", 6697), "NickServ"],
    "NordNet":          [("irc.nordnet.org", 6667), None],
    "Omegaserv":        [("irc.omegaserv.net", 6667), "NickServ"],
    "PixelZone":        [("irc.pixelzone.net", 6697), "NickServ"],
    "PTnet":            [("irc.ptnet.org", 6697), "NickServ"],
    "RizonEU":          [("eu.rizon.net", 6697), "NickServ"],
    "RubyNet":          [("irc.ruby.net", 6697), "NickServ"],
    "SWECLAN":          [("irc.sweclan.org", 6697), "NickServ"],
    "TrekNet":          [("irc.treknet.org", 6667), "NickServ"],
    "UnderNet-DE":      [("irc.undernet.de", 6667), "X"],
    "Undernet-UK":      [("irc.undernet.uk", 6667), "X"],
    "Vortex":           [("irc.vortexirc.net", 6697), "NickServ"],
    "WeaselNet":        [("irc.weaselnet.org", 6667), "NickServ"],
    "Wolfnet":          [("irc.wolfnet.net", 6667), "NickServ"],
    "ZNC":              [("irc.znc.in", 6697), "NickServ"],
    "ZenithNet":        [("irc.zenithnet.net", 6667), "NickServ"],
    "Zircon":           [("irc.zircon.net", 6697), "NickServ"],

    # Gaming and niche communities
    "MinecraftNet":     [("irc.minecraft.net", 6667), "NickServ"],
    "ZeldaNet":         [("irc.zeldanet.org", 6667), "NickServ"],
    "TF2Net":           [("irc.tf2net.com", 6697), "NickServ"],
    "SteamIRC":         [("irc.steamcommunity.com", 6667), None],
    "WoWNet":           [("irc.wownet.org", 6697), "NickServ"],

    # Regional IRC Networks
    "ChatNet":          [("irc.chatnet.org", 6667), "NickServ"],
    "IranIRC":          [("irc.irannetwork.org", 6697), "NickServ"],
    "AsiaNet":          [("irc.asianet.org", 6667), "NickServ"],
    "EuroNet":          [("irc.euronet.org", 6697), "NickServ"],
    "JapanNet":         [("irc.japan.net", 6667), "NickServ"],
    "KoreaNet":         [("irc.koreanet.org", 6697), "NickServ"],
    "LatinNet":         [("irc.latinnet.org", 6697), "NickServ"],

    # Other niche and tech communities
    "PythonNet":        [("irc.python.org", 6667), "NickServ"],
    "LinuxNet":         [("irc.linux.org", 6667), "NickServ"],
    "NodeNet":          [("irc.nodenet.org", 6697), "NickServ"],
    "Rustaceans":       [("irc.rustaceans.org", 6667), "NickServ"],
}

# Global state for tracking connection status
connected_status = {}
connected_status_lock = threading.Lock()

# Failed Auth Dictionary
failed_auth = {}

def wait_for_identified(irc, timeout=15):
    """
    Waits for and checks IRC server messages for a successful or failed
    authentication. This is a critical step because authentication is asynchronous.
    """
    buffer = ""
    end = time.time() + timeout
    irc.settimeout(2)
    while time.time() < end:
        try:
            chunk = irc.recv(4096).decode("utf-8", errors="ignore")
            if not chunk:
                break
            buffer += chunk
            for line in chunk.strip().split("\r\n"):
                print(f"[IRC] >> {line}")
                lower_line = line.lower()
                # Keywords for successful authentication
                if any(kw in lower_line for kw in ["identified", "logged in", "password accepted", "authentication successful"]):
                    return True
                # Keywords for failed authentication
                if any(kw in lower_line for kw in ["invalid password", "incorrect", "authentication failed", "not recognized"]):
                    return False
        except socket.timeout:
            continue
        except Exception:
            break
    return False

def send_auth_command(irc, service, nick, password):
    """
    Sends the appropriate authentication command to the IRC service.
    """
    print(f"[Auth] Attempting to authenticate with {service}...")
    if service == "NickServ":
        irc.sendall(f"PRIVMSG NickServ :IDENTIFY {password}\r\n".encode())
    elif service == "X":
        irc.sendall(f"PRIVMSG X@channels.undernet.org :LOGIN {nick} {password}\r\n".encode())
    elif service == "Q":
        irc.sendall(f"PRIVMSG Q@CServe.quakenet.org :AUTH {nick} {password}\r\n".encode())
    elif service == "AuthServ":
        irc.sendall(f"PRIVMSG AuthServ@services.gamesurge.net :AUTH {nick} {password}\r\n".encode())
    else:
        print(f"[Auth] No known authentication method for service: {service}")

def send_register_command(irc, service, nick, password, email):
    """
    Sends the appropriate registration command to the IRC service.
    """
    print(f"[Auth] Attempting to register with {service}...")
    if service == "NickServ":
        irc.sendall(f"PRIVMSG NickServ :REGISTER {password} {email}\r\n".encode())
    elif service == "Q":
        irc.sendall(f"PRIVMSG Q@CServe.quakenet.org :REGISTER {email} {password}\r\n".encode())
    elif service == "X":
        irc.sendall(f"PRIVMSG X@channels.undernet.org :REGISTER {password} {email}\r\n".encode())
    elif service == "AuthServ":
        irc.sendall(f"PRIVMSG AuthServ@services.gamesurge.net :REGISTER {password} {email}\r\n".encode())
    else:
        print(f"[{service}] Does not support a known REGISTER command.")

def handle_sasl_auth(irc, nick, password):
    """
    Handles SASL PLAIN authentication before the 001 welcome message.
    """
    try:
        # Request SASL capability
        irc.sendall("CAP REQ :sasl\r\n".encode())
        print("[SASL] << CAP REQ :sasl")

        # Wait for the server to acknowledge the capability
        buffer = ""
        sasl_ack = False
        start_time = time.time()
        while time.time() - start_time < 5:
            data = irc.recv(4096).decode("utf-8", errors="ignore")
            buffer += data
            for line in data.strip().split("\r\n"):
                print(f"[SASL] >> {line}")
                if "ACK" in line and "sasl" in line:
                    sasl_ack = True
                    break
            if sasl_ack:
                break
        
        if not sasl_ack:
            print("[SASL] ❌ Server did not acknowledge SASL. Continuing without it.")
            return False

        # Send authentication credentials
        auth_string = base64.b64encode(f"\x00{nick}\x00{password}".encode()).decode()
        irc.sendall(f"AUTHENTICATE PLAIN\r\n".encode())
        print("[SASL] << AUTHENTICATE PLAIN")

        # Wait for '+' from server
        data = irc.recv(1024).decode("utf-8", errors="ignore")
        print(f"[SASL] >> {data.strip()}")
        if "+" in data:
            irc.sendall(f"AUTHENTICATE {auth_string}\r\n".encode())
            print("[SASL] << AUTHENTICATE <base64_string>")
            
            # Wait for SASL success or failure
            sasl_result = ""
            start_time = time.time()
            while time.time() - start_time < 5:
                data = irc.recv(4096).decode("utf-8", errors="ignore")
                sasl_result += data
                if "904" in sasl_result: # SASL authentication failed
                    print("[SASL] ❌ SASL authentication failed.")
                    return False
                elif "903" in sasl_result: # SASL authentication successful
                    print("[SASL] ✅ SASL authentication successful.")
                    return True
        else:
            print("[SASL] ❌ Server did not send '+'.")
            return False

    except Exception as e:
        print(f"[SASL] ❌ SASL Error: {e}")
        return False
    finally:
        # End capability negotiation
        irc.sendall("CAP END\r\n".encode())
        print("[SASL] << CAP END")
    return False

def irc_thread(net_name, server, port, auth_services):
    print(f"\n--- Connecting to {net_name} ---")
    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_sock.settimeout(15)

    try:
        if port == 6697:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            irc = context.wrap_socket(raw_sock, server_hostname=server)
        else:
            irc = raw_sock

        irc.connect((server, port))
        irc.settimeout(5)

        authenticated = False

        if "SASL" in auth_services:
            if handle_sasl_auth(irc, nickname, password):
                authenticated = True

        irc.sendall(f"NICK {nickname}\r\n".encode())
        irc.sendall(f"USER {nickname} 0 * :{nickname}\r\n".encode())

        registered = False
        buffer = ""

        while True:
            try:
                data = irc.recv(4096).decode("utf-8", errors="ignore")
                if not data:
                    print(f"[{net_name}] Connection closed by server.")
                    break
                buffer += data
                lines = buffer.split("\r\n")
                buffer = lines.pop()

                for line in lines:
                    print(f"[{net_name}] >> {line}")

                    if line.startswith("PING"):
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            irc.sendall(f"PONG :{parts[1].strip()}\r\n".encode())
                            print(f"[{net_name}] << PONG :{parts[1].strip()}")

                    if " 433 " in line and nickname in line:
                        print(f"[{net_name}] ❌ Nickname '{nickname}' already in use on server.")
                        with connected_status_lock:
                            connected_status.pop(net_name, None)
                            failed_auth[net_name] = "Nickname already in use"
                        irc.sendall(f"QUIT :Nickname already in use\r\n".encode())
                        irc.close()
                        return

                    if " 001 " in line and not registered:
                        print(f"[{net_name}] ✅ Connected to IRC network.")
                        registered = True

                        if not authenticated and len(auth_services) > 1 and auth_services[1]:
                            post_auth_service = auth_services[1]

                            # 1. Try IDENTIFY
                            send_auth_command(irc, post_auth_service, nickname, password)
                            if wait_for_identified(irc):
                                print(f"[{net_name}] ✅ Successfully authenticated with {post_auth_service}.")
                                authenticated = True
                            else:
                                print(f"[{net_name}] ❌ Authentication failed with {post_auth_service}. Trying to REGISTER...")
                                
                                # 2. Try REGISTER
                                send_register_command(irc, post_auth_service, nickname, password, email)
                                time.sleep(10)  # Some networks need more time to process registration

                                # 3. Try IDENTIFY again
                                send_auth_command(irc, post_auth_service, nickname, password)
                                if wait_for_identified(irc):
                                    print(f"[{net_name}] ✅ Authenticated after registration.")
                                    authenticated = True
                                else:
                                    print(f"[{net_name}] ❌ Still unauthenticated after REGISTER. Closing connection.")
                                    irc.sendall("QUIT :Authentication failed\r\n".encode())
                                    irc.close()
                                    with connected_status_lock:
                                        connected_status.pop(net_name, None)
                                        failed_auth[net_name] = "Authentication failed or nick in use"
                                    return

                        with connected_status_lock:
                            connected_status[net_name] = authenticated

                        if authenticated or not auth_services:
                            irc.sendall(f"JOIN {channel_name}\r\n".encode())
                        else:
                            print(f"[{net_name}] ❌ Failed to authenticate. Closing connection.")
                            irc.sendall("QUIT :Authentication required but failed\r\n".encode())
                            irc.close()
                            with connected_status_lock:
                                connected_status.pop(net_name, None)
                            return

                    if f"JOIN :{channel_name}" in line and nickname.lower() in line.lower():
                        print(f"[{net_name}] ✅ Joined channel {channel_name}")

                    if f"PRIVMSG {nickname}" in line:
                        prefix = line.split(" ")[0]
                        if "!" in prefix:
                            sender_nick = prefix[1:].split("!")[0]
                            irc.sendall(f"PRIVMSG {sender_nick} :echo\r\n".encode())
                            print(f"[{net_name}] Sent 'echo' to {sender_nick}")

            except socket.timeout:
                continue
            except Exception as e:
                print(f"[{net_name}] ❌ Error: {e}")
                break

    except Exception as e:
        print(f"[{net_name}] ❌ Connection error: {e}")
    finally:
        try:
            irc.close()
        except Exception:
            pass
        with connected_status_lock:
            connected_status.pop(net_name, None)
        print(f"[{net_name}] Disconnected.")


def report_status_loop():
    """Reports the status of connected and authenticated servers every 60 seconds."""
    while True:
        time.sleep(60)
        with connected_status_lock:
            authenticated_servers = {
                net: auth for net, auth in connected_status.items() if auth
            }
            if authenticated_servers:
                print("\n=== Live & Authenticated Servers Report ===")
                for net in authenticated_servers:
                    print(f" - {net}: Authenticated")
                print("============================================\n")
            else:
                print("\n=== No servers currently authenticated ===\n")

            if failed_auth:
                print("\n=== Nickname-In-Use Networks ===")
                for net, reason in failed_auth.items():
                    print(f" - {net}: {reason}")
                print("============================================\n")

def main():
    """Main function to start threads for all IRC networks."""
    threads = []
    for net_name, (server_info, *auth_services) in irc_networks.items():
        t = threading.Thread(target=irc_thread, args=(net_name, server_info[0], server_info[1], auth_services), daemon=True)
        t.start()
        threads.append(t)

    try:
        report_status_loop()
    except KeyboardInterrupt:
        print("Exiting...")

if __name__ == "__main__":
    main()