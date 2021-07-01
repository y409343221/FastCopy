import logging
from binascii import crc32
from queue import Queue, Empty
from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE
from socket import socket, timeout, MSG_WAITALL
from struct import pack, unpack
from threading import Thread
from typing import Any, List, NamedTuple, Set, Tuple

from const import EOF, Flag, LEN_HEAD


class Packet(NamedTuple):
    flag: Flag
    body: bytes

    def __str__(self) -> str:
        return f'Flag: {self.flag} Len={self.length}'

    @property
    def length(self) -> int:
        return len(self.body)

    @property
    def chksum(self) -> int:
        return crc32(self.body)

    @staticmethod
    def load(flag: Flag, *args) -> 'Packet':
        '''将包体封包'''
        if flag == Flag.PULL or flag == Flag.PUSH:
            if isinstance(args[0], bytes):
                body = args[0]
            else:
                body = str(args[0]).encode('utf8')
        elif flag == Flag.SID or flag == Flag.ATTACH:
            body = pack('>H', *args)
        elif flag == Flag.FILE_COUNT:
            body = pack('>H', *args)
        elif flag == Flag.DIR_INFO:
            length = len(args[-1])
            body = pack(f'>2H{length}s', *args)
        elif flag == Flag.FILE_INFO:
            length = len(args[-1])
            body = pack(f'>2HQd16s{length}s', *args)
        elif flag == Flag.FILE_READY:
            body = pack('>H', *args)
        elif flag == Flag.FILE_CHUNK:
            length = len(args[-1])
            body = pack(f'>HI{length}s', *args)
        elif flag == Flag.DONE:
            body = pack('>I', EOF)
        elif flag == Flag.RESEND:
            body = pack('>BIH', *args)
        else:
            raise ValueError('Invalid flag')
        return Packet(flag, body)

    def pack(self) -> bytes:
        '''封包'''
        fmt = f'>BIH{self.length}s'
        return pack(fmt, self.flag, self.chksum, self.length, self.body)

    @staticmethod
    def unpack_head(head: bytes) -> Tuple[Flag, int, int]:
        '''解析 head'''
        flag, chksum, length = unpack('>BIH', head)
        return Flag(flag), chksum, length

    def unpack_body(self) -> Tuple[Any, ...]:
        '''将 body 解包'''
        if self.flag == Flag.PULL or self.flag == Flag.PUSH:
            return (self.body.decode('utf-8'),)  # dest path

        elif self.flag == Flag.SID or self.flag == Flag.ATTACH:
            return unpack('>H', self.body)  # Worker ID

        elif self.flag == Flag.FILE_COUNT:
            return unpack('>H', self.body)  # file count

        elif self.flag == Flag.DIR_INFO:
            # file_id | perm | path
            #   2B    |  2B  |  ...
            fmt = f'>2H{self.length - 4}s'
            return unpack(fmt, self.body)

        elif self.flag == Flag.FILE_INFO:
            # file_id | perm | size | mtime | chksum | path
            #   2B    |  2B  |  8B  |  8B   |  16B   |  ...
            fmt = f'>2HQd16s{self.length - 36}s'
            return unpack(fmt, self.body)

        elif self.flag == Flag.FILE_READY:
            return unpack('>H', self.body)  # file id

        elif self.flag == Flag.FILE_CHUNK:
            # file_id |  seq  | chunk
            #    2B   |  4B   |  ...
            fmt = f'>HI{self.length - 6}s'
            return unpack(fmt, self.body)

        elif self.flag == Flag.DONE:
            return unpack('>I', self.body)

        elif self.flag == Flag.RESEND:
            return unpack('>BIH', self.body)

        else:
            raise TypeError

    def is_valid(self, chksum: int):
        '''是否是有效的包体'''
        return self.chksum == chksum


class ConnectionPool:
    _max_size = 128
    _timeout = 0.001  # 1ms

    def __init__(self, size: int) -> None:
        self.size = min(size, self._max_size)
        # 发送、接收队列
        self.send_q: Queue[Packet] = Queue(self.size * 5)
        self.recv_q: Queue[Packet] = Queue(self.size * 5)

        # 所有 Socket
        self.socks: Set[socket] = set()

        # 发送、接收多路复用
        self.sender = DefaultSelector()
        self.receiver = DefaultSelector()

        self.is_working = True
        self.threads: List[Thread] = []

    def send(self, packet: Packet):
        '''发送'''
        self.send_q.put(packet)

    def recv(self, block=True, timeout=None) -> Packet:
        '''接收'''
        return self.recv_q.get(block, timeout)

    def add(self, sock: socket):
        '''添加 sock'''
        if len(self.socks) < self.size:
            self.socks.add(sock)
            self.sender.register(sock, EVENT_WRITE)
            self.receiver.register(sock, EVENT_READ, bytearray())
            return True
        else:
            return False

    def remove(self, sock: socket):
        '''删除 sock'''
        self.sender.unregister(sock)
        self.receiver.unregister(sock)
        self.socks.remove(sock)
        sock.close()

    def _send(self):
        '''从 send_q 获取数据，并封包发送到对端'''
        while self.is_working:
            for key, _ in self.sender.select():
                try:
                    packet = self.send_q.get(timeout=0.2)
                except Empty:
                    continue

                try:
                    # 发送数据
                    msg = packet.pack()
                    key.fileobj.send(msg)
                    logging.debug(f'[Send] {packet.flag.name}: length={packet.length} chksum={packet.chksum}')
                except Exception as e:
                    # 若发送失败，则将 packet 放回队列首位
                    self.send_q.queue.appendleft(packet)
                    if isinstance(e, ConnectionResetError):
                        # 对端已断开，则从本端删除此连接
                        self.remove(key.fileobj)
                    logging.error(f'SendErr: {e} | {packet.flag.name}: length={packet.length} chksum={packet.chksum}')

    def _recv(self):
        '''接收并解析数据包, 解析结果存入 recv_q 队列'''
        while self.is_working:
            for key, _ in self.receiver.select(timeout=0.2):
                try:
                    # 从缓存或 sock 取出包头
                    head = key.data or key.fileobj.recv(LEN_HEAD, MSG_WAITALL)
                except timeout:
                    continue

                # 若数据为空，关闭连接
                if not head:
                    self.remove(key.fileobj)
                    continue

                try:
                    # 解析包头，并接收包体
                    flag, chksum, length = Packet.unpack_head(head)
                    body = key.fileobj.recv(length, MSG_WAITALL)
                    key.data.clear()
                except timeout:
                    key.data.extend(head)
                    continue

                # 检查报文有效性
                packet = Packet(flag, body)
                if packet.is_valid(chksum):
                    logging.debug(f'[Recv] {packet.flag.name}: length={packet.length} chksum={packet.chksum}')
                    self.recv_q.put(packet)  # 正确的数据包放入队列
                else:
                    logging.error('丢弃错误包，请求重传')
                    resend_packet = Packet.load(Flag.RESEND, flag, chksum, length)
                    self.send_q.put(resend_packet)

    def start(self):
        '''启动连接池的发送和接收线程'''
        s_thread = Thread(target=self.send, daemon=True)
        s_thread.start()
        self.threads.append(s_thread)

        r_thread = Thread(target=self.recv, daemon=True)
        r_thread.start()
        self.threads.append(r_thread)

    def stop(self):
        '''关闭所有连接'''
        self.is_working = False
        for t in self.threads:
            t.join()

        self.sender.close()
        self.receiver.close()

        for sock in self.socks:
            sock.close()

        logging.info('[ConnPool] closed all connections.')


class NetworkMixin:
    sock: socket

    def send_msg(self, flag: Flag, *args):
        '''发送数据报文'''
        packet = Packet.load(flag, *args)
        datagram = packet.pack()
        logging.debug('<- %r' % datagram)
        self.sock.send(datagram)

    def recv_msg(self) -> Packet:
        '''接收数据报文'''
        # 接收并解析 head 部分
        head = self.sock.recv(LEN_HEAD, MSG_WAITALL)
        flag, chksum, len_body = Packet.unpack_head(head)
        logging.debug(f'-> {flag}: length={len_body} {chksum=}')

        if not Flag.contains(flag):
            raise ValueError('unknow flag: %d' % flag)

        # 接收 body 部分
        body = self.sock.recv(len_body, MSG_WAITALL)

        # 错误重传
        if crc32(body) != chksum:
            self.send_msg(Flag.RESEND, head)
            return self.recv_msg()

        return Packet(flag, body)
