# ADR-02：用 SQLCipher 做整库加密

- 状态：**已定，Windows 已验证；macOS / Linux 待 CI 矩阵补齐**
- 日期：2026-10-15
- 相关：第 7.2 节（密钥与加密）、第 4.1 节（PRAGMA）、第 14 章 P0 任务 2、附录 A 第 2 天

## 背景

每个档案一个独立数据库文件（ADR-01）。这些文件会躺在磁盘上几十年，可能被拷走、
被同步到网盘、被别的程序读到。所以要求是：**不输入口令，直接读文件字节，看不到任何明文**。

候选方案：

1. SQLCipher 整库加密（本 ADR 采纳）。
2. SQLite + 应用层整库文件加密（打开时解密到内存或临时加密卷）。

## 决策

用 **SQLCipher** 做整库加密。密钥用 DEK 的十六进制原始密钥形式交给 SQLCipher
（`PRAGMA key = "x'…'"`），不经口令派生——口令派生只发生在打开 DEK 的那一步（Argon2id），
数据库连接本身不认识口令。

选它的理由：

- **加密与查询在同一层完成**。FTS5、触发器、视图、递归 CTE 都能照常用，不需要把整库解密到内存。
- 备选方案要把整库解密到内存或临时盘，等于在运行期把明文再写一份到磁盘上，
  和「静态数据全部加密」的初衷相冲突（第 7 章）。
- Windows / macOS / Linux 都有官方或社区维护的构建，`pip install sqlcipher3` 即可。

## 验证

验证脚本：[test_sqlcipher_smoke.py](file:///d:/AI%E8%BD%AF%E4%BB%B6/LifeOS/services/core/tests/test_sqlcipher_smoke.py)，
逐项对应附录 A 第 2 天要求的「开库、写读、FTS5 trigram、备份接口」。

| 验证项 | 结果 |
| --- | --- |
| 能安装 | 通过（Windows，`sqlcipher3` 0.6.4 的 `cp314-win_amd64` wheel，纯二进制、无本地编译） |
| 能开库、能用密钥读写 | 通过 |
| 原始字节无明文 | 通过（写入中文密文后扫描整个文件字节，未出现明文） |
| 错误密钥被拒 | 通过（`DatabaseError`，而不是静默返回空结果） |
| FTS5 trigram 可用 | 通过（中文子串 `失眠导致` 命中） |
| 备份接口可用 | 通过（`Connection.backup()` 产出的副本可用同一密钥打开） |

环境：`python 3.14.0`、`sqlcipher3 0.6.4`、`SQLCipher 4.19.0 community`、`SQLite 3.53.4`。

## 覆盖率说明（不粉饰）

上面这份结论**只在 Windows 上跑过**。macOS 与 Linux 的结论由 CI 矩阵补齐
（[ci.yml](file:///d:/AI%E8%BD%AF%E4%BB%B6/LifeOS/.github/workflows/ci.yml) 的 `backend` 任务在
ubuntu / windows / macos 三个 runner 上跑同一份测试）。三平台全绿之前，
本 ADR 的状态就一直是「部分验证」；若任一平台失败，按下面的备选方案降级。

## 备选方案（仅在某个平台装不上或跑不通时启用）

改用 **SQLite + 应用层整库文件加密**：文件以密文存放，解锁时解密到一个内存文件系统
（Linux/macOS 用 tmpfs，Windows 用 RAM 盘或临时目录 + 退出即擦除），关锁时回写并清零。
代价是失去 FTS5 与触发器的直接用武之地（要么先解密再跑，要么退化成应用层搜索），
以及多出一份「运行期明文」的暴露面。**这是降级，不是等价方案。**

## 影响

- `lifeos/db/connection.py` 里固定的 PRAGMA 集合：`cipher_memory_security=ON`、
  `secure_delete=ON`、`journal_mode=WAL`、`foreign_keys=ON`（第 4.1 节）。
- 备份、迁移回滚都基于加密文件本身，不需要额外解密步骤（第 4.6 节）。
- 换算法或换参数属于会改变文件格式的改动：必须新开一个 ADR，并要求迁移路径能读旧文件。
