# FastCopy

远程文件快速复制、同步工具。

## 报文设计

所有数据包均采用**大端字节序**

### 1. 报文统一格式

|  flag   | chksum  | length  | payload |
| :-----: | :-----: | :-----: | :-----: |
| 1 Bytes | 4 Bytes | 2 Bytes |   ...   |

### 2. 报文类型

1. 拉取申请: `0x01`
2. 推送申请: `0x02`
3. 建立会话: `0x03`
4. 后续连接: `0x04`
5. 文件总量: `0x05`
6. 文件信息: `0x06`
7. 文件就绪: `0x07`
8. 数据传输: `0x08`
9. 错误回传: `0x09`

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


## 0.3 版目标

- [x] SSH Tunnel 支持
- [x] 合并: Sender + Writer, Receiver + Reader
- [x] 使用多线程改写 ConnectPool
- [ ] 完善的错误处理
- [ ] Server 执行中的错误需要反馈到 Client
- [ ] 服务器异常退出前，给客户端发送异常原因

## 0.4 版目标
- [ ] 链接的处理：链接文件如果也在传输的文件中，则保持链接状态，否则直接传输原文件
- [ ] 断点续传支持


### 握手阶段

|        客户端        |               服务器               |
| :------------------: | :--------------------------------: |
|      客户端启动      |             服务器启动             |
|     发起连接请求     |                                    |
|                      |  等待客户端命令，三秒无响应则断开  |
|     发送拉取命令     |                                    |
|                      |           产生 SessionID           |
|                      |            整理文件信息            |
|                      | 将 SessionID 和 文件信息传回客户端 |
|      建立空文件      |                                    |
|      开始 recv       |                                    |
|  通知服务器开始发送  |                                    |
| 创建更多线程接收数据 |                                    |
|                      |                                    |
|                      |                                    |

1. 客户端、服务器建立连接
2. 客户端发送 拉取/推送 命令
3. 服务器产生 SessionID，并发送给客户端
4. 客户端建立并发连接，新连接携带 Session


### 传输流程

```
Sender                  Receiver
--------------------------------
traverse src tree
n_file_and_dir  ->
infos of tree   ->
                        mkdirs
                        mkfiles
                        checkfiles
```
