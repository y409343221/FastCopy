# FastCopy

基于 SSH 协议的多线程远程文件快速传输工具。
目标是用来替换 SCP。

## 安装

```shell
pip install fast-copy
```

## 使用

1. 服务器

    ```shell
    fcpd -d
    ```

2. 本地

    ```shell
    # download
    fcp user@host:/foo/bar ./

    # upload
    fcp ./fake/file user@host:/foo/bar
    ```

## 报文设计

所有数据包均采用**大端字节序**

### 1. 报文统一格式

|  flag   | chksum  | length  | payload |
| :-----: | :-----: | :-----: | :-----: |
| 1 Bytes | 4 Bytes | 2 Bytes |   ...   |

### 2. 报文类型

1. 推送申请: `0x1`
2. 拉取申请: `0x2`
3. 建立会话: `0x3`
4. 后续连接: `0x4`
5. 传输模式: `0x5`
6. 目录信息: `0x6`
7. 文件信息: `0x7`
8. 文件数量: `0x8`
9. 文件就绪: `0x9`
10. 数据传输: `0xa`
11. 完成: `0xb`
12. 错误回传: `0xc`
13. 异常退出: `0xd`


### 3. 报文详情

1. 数据请求

    连接建立后，客户端首先需要向服务器申请 *拉取* 或 *推送*，并将 *目的路径* 传给服务器

    - 拉取、推送的标识由 `flag` 字段决定
    - 方向: Client -> Server
    - Payload 格式为:

        | dst_path |
        | :------: |
        |   ...    |

2. 建立会话

    服务器收到第一步的申请后，会产生一个 SessionID，并回传给客户端，客户端需要在自己本地保存

    - 方向: Server -> Client
    - Payload 格式为:

        | session_id |
        | :--------: |
        |  16 Bytes  |

3. 后续连接

    客户端后续与服务器建立的并发连接，第一个报文须告诉服务器 SessionID

    - 方向: Client -> Server
    - Payload 格式为:

        | session_id |
        | :--------: |
        |  16 Bytes  |

4. 文件总量

    连接就绪后，发送端需告知接收端文件总量

    - Payload 长度 4 字节，所以最大允许传输文件数量为 4,294,967,296
    - 方向: Sender -> Receiver
    - Payload 格式:

        | n_files |
        | :-----: |
        | 4 Bytes |

5. 文件信息

    文件发送发需将每一个文件的信息告知接收端。
    包括文件的编号、权限、大小、创建时间、修改时间、访问时间、校验和、路径。
    其中路径为相对路径。

    - 方向: Sender -> Receiver
    - Payload 格式:

        | file_id |  perm   |  size   |  mtime  |  chksum  | path  |
        | :-----: | :-----: | :-----: | :-----: | :------: | :---: |
        | 4 Bytes | 2 Bytes | 8 Bytes | 8 Bytes | 16 Bytes |  ...  |

6. 接收端文件准备就绪

    接收端收到文件信息后，需将文件信息记录起来，并在本地创建同样大小的空文件

    - 方向: Receiver -> Sender
    - Payload 格式:

        | file_id |
        | :-----: |
        | 4 Bytes |

7. 文件数据块传输报文

    Chunk Sequence 占用 4 字节，所以支持的单个文件最大为: 4 GB * ChunkSize

    - 方向: Sender -> Receiver
    - Payload 格式:

        | file_id |   seq   | data  |
        | :-----: | :-----: | :---: |
        | 4 Bytes | 4 Bytes |  ...  |

8. 错误回传报文

    - 方向: Receiver -> Sender

    - Payload 格式:

        |   seq   |
        | :-----: |
        | 4 Bytes |


## TODO

- Server 执行中的错误需要反馈到 Client
- 服务器异常退出前，给客户端发送异常原因
- 断点续传支持


### 握手阶段

| 序号 |                  客户端                   |               服务器                |
| ---- | :---------------------------------------: | :---------------------------------: |
| 1    |                客户端启动                 |             服务器启动              |
| 2    |               发起连接请求                |                                     |
| 3    |                                           |           接收客户端请求            |
| 4    |                                           |  等待客户端请求，超时无响应则断开   |
| 5    |          发送`PUSH`或`PULL`请求           |                                     |
| 6    |                                           |           产生 SessionID            |
| 7    |                                           |       将 SessionID 传回客户端       |
| 8    |           接收 SessionID 并保存           |                                     |
| 9    |           循环创建多个并行连接            |                                     |
| 10   | 新连接携带 SessionID 逐一发送`ATTACH`请求 |                                     |
| 11   |                                           |         确认 SessionID 无误         |
| 12   |                                           | 将新连接添加至对应 Session 的连接池 |
