#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.2.1 quota provider identity 指纹共享模块
（v2.2.1 WU-221-B1：compute_provider_identity_hash 自 watcher.py
纯移动至此，watcher / primer 共用同一派生口径）。

职责：
    provider identity 指纹的唯一派生落点：sha256("来源:凭证")
    十六进制前 16 位——watcher 侧 §6 单实例锁身份字段（watcher.lock
    的 provider_identity_hash）与 primer 侧幂等键之二共用的口径。

非秘密性（本函数返回值的安全属性）：
    返回值是 **非秘密** 的 16 位十六进制指纹——sha256 不可逆前缀
    截断，不含也不可还原凭证材料，可安全落日志 / 落盘（既有实践：
    watcher.lock / window_primed journal 事件均落此指纹）；原始凭证
    材料则绝不落日志 / 落盘（凭证经 resolve_credential 只进内存，
    本模块零 print / 零 log，异常与返回值均不携带 key 本体——
    runtime/quota/_http.py §37 安全条款 / 升级指南 §36-§38）。

无凭证语义（冻结）：
    api_key is None → material = "none:"（"none" 来源 + 空串凭证）
    的确定性占位哈希——watcher 仍可 PASSIVE 运行，acquire 语义
    不受影响。

依赖：
    仅 Python 3.8 标准库（hashlib）+ runtime.quota.credentials
    （保持抽取前形态：函数体内惰性 import，导入时机不变）；
    零第三方依赖。

来源：
    v2.2.1 WU-221-B1（pure move：签名 / environ 处理 / 16-hex 输出
    与原 watcher.py 实现逐字节同语义）。
"""

import hashlib


def compute_provider_identity_hash(environ=None):
    """provider identity 指纹（§6 单实例锁身份字段）：sha256("来源:凭证")
    十六进制前 16 位。

    只落哈希绝不落凭证材料（runtime/quota/_http.py §37 安全条款 /
    升级指南 §36-§38——哈希不可逆，不含 key 本体）；无凭证
    → ("none" 来源的确定性占位哈希)——watcher 仍可 PASSIVE 运行，
    acquire 语义不受影响。
    """
    from runtime.quota.credentials import resolve_credential
    _source, api_key = resolve_credential(environ=environ)
    material = "%s:%s" % ("none" if api_key is None else "credential",
                          api_key or "")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
