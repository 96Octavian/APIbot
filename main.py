import json
import os
import platform
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Dict, Optional

from telegram.error import NetworkError
from telegram.ext import Updater, Filters, MessageHandler, CommandHandler

# TODO: Implement a logging facility

# Telegram upload limit for a bot is 50 MB
MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024

users: Dict[str, Dict[str, int]] = dict()

ADMIN: int = int()


# Log errors
def error_logger(update, context) -> None:
    """Log Errors caused by Updates."""
    text: str = f'Error "{context.error}"'
    print(text)


def pid(update, context) -> None:
    if str(update.message.from_user.id) != ADMIN:
        return
    text: str = f'PID: {os.getpid()}'
    update.message.reply_text(text)


def get_user(update) -> Dict[str, int]:
    from_id = str(update.message.from_user.id)

    if from_id not in users:
        users[from_id] = dict()
        users[from_id]['executions'] = int()
        users[from_id]['input_size'] = int()
        users[from_id]['output_size'] = int()
        users[from_id]['processing_time'] = int()
        users[from_id]['crashes'] = int()
        users[from_id]['executions_killed'] = int()

    return users[from_id]


def write() -> None:
    with open('./users.json', 'w') as json_file:
        json.dump(users, json_file, sort_keys=True, indent=4, separators=(',', ': '))


def file_handler(update, context) -> None:
    user: Dict[str, int] = get_user(update)

    with tempfile.TemporaryFile() as temp_in, tempfile.TemporaryFile() as temp_out:
        user['executions'] += 1
        file = context.bot.get_file(update.message.document.file_id)
        file.download(out=temp_in)
        user['input_size'] += temp_in.tell()
        temp_in.seek(0)
        try:
            time_start = time.time_ns()
            cp = subprocess.run(["./edU.exe"], timeout=30, stdin=temp_in, stdout=temp_out)
            if cp.returncode != 0:
                time_end = time.time_ns()
                user['executions_killed'] += 1
                update.message.reply_text(f'Execution killed with exit code: {cp.returncode}')
            else:
                time_end = time.time_ns()
                user['output_size'] += temp_out.tell()
                if temp_out.tell() == 0:
                    update.message.reply_text("Empty output. Maybe there are no prints in input?")
                elif temp_out.tell() > MAX_UPLOAD_SIZE:
                    update.message.reply_text("Output file too big for Telegram limits")
                else:
                    temp_out.seek(0)
                    context.bot.send_document(chat_id=update.message.from_user.id, document=temp_out,
                                              filename="output.txt")

        except subprocess.TimeoutExpired:
            time_end = time.time_ns()
            update.message.reply_text(f'Execution took more than 30 seconds, killed')
        except NetworkError:
            try:
                update.message.reply_text("Network timeout hit, output upload may take a while")
                temp_out.seek(0)
                context.bot.send_document(chat_id=update.message.from_user.id, document=temp_out, filename="output.txt",
                                          timeout=600)
            except NetworkError:
                update.message.reply_text(
                    "The output took too long to update, network timeout hit. The output is too big")

        user['processing_time'] += time_end - time_start

        write()


class SocketDatagramHandler(socketserver.BaseRequestHandler):

    def handle(self):
        # Receive the data
        print("Receiving...")
        data = self.request[1].recv(1024)
        print(f'Received:\n{data}')
        return


def send_msg(sock, msg):
    # Prefix each message with a 4-byte length (network byte order)
    msg = struct.pack('>I', len(msg)) + msg
    sock.sendall(msg)


class SocketStreamHandler(socketserver.BaseRequestHandler):

    def recvall(self, n: int) -> Optional[bytearray]:
        # Helper function to recv n bytes or return None if EOF is hit
        data: bytearray = bytearray()
        while len(data) < n:
            packet = self.request.recv(n - len(data))
            if not packet:
                return None
            data.extend(packet)
        return data

    def recv_msg(self):
        # Read message length and unpack it into an integer
        raw_msglen = self.recvall(4)
        if not raw_msglen:
            return None
        msglen = struct.unpack('>I', raw_msglen)[0]
        # Read the message data
        return self.recvall(msglen)

    def handle(self):
        # Receive the data
        print("Receiving...")
        complete_data = self.recv_msg()
        print(f'Received:\n{complete_data}')
        return


def main():
    global users
    global ADMIN
    try:
        with open('./users.json', 'r', encoding='utf-8') as file:
            users = json.load(file)
    except FileNotFoundError:
        pass

    ADMIN = sys.argv[2]

    is_linux = platform.system().lower() == 'linux'

    if is_linux:

        server_address: str = './apibot.sock'

        try:
            os.unlink(server_address)
        except OSError:
            if os.path.exists(server_address):
                raise

        server = socketserver.UnixStreamServer(server_address, SocketStreamHandler)

        # Start the server in a thread
        t = threading.Thread(target=server.serve_forever)
        t.setDaemon(True)  # don't hang on exit
        t.start()

    # Create the Updater and pass it your bot's token.
    updater = Updater(sys.argv[1], use_context=True)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    dp.add_handler(MessageHandler(Filters.document & Filters.chat_type.private, file_handler))
    dp.add_handler(CommandHandler("pid", pid))

    # log all errors
    dp.add_error_handler(error_logger)

    # Start the Bot
    updater.start_polling()

    updater.bot.sendMessage(ADMIN, "Bot started")
    print(f'PID: {os.getpid()}')
    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()

    if is_linux:
        server.shutdown()

    # logger.warning("Shutting down...")
    return


if __name__ == '__main__':
    main()
