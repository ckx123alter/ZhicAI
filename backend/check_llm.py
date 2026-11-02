"""Opt-in live provider smoke test. No database writes or financial operations.

Run after setting backend/.env: python -m backend.check_llm
This makes two real model requests and consumes API tokens.
"""
import sys
from fastapi import HTTPException
from .app.config import load_settings
from .app.agent.chat import interpret
from .app.agent.narrative import generate
from .app.agent.schemas import AgentResponse


def main():
    try:
        settings=load_settings()
        if settings is None:
            raise RuntimeError('未选择模型 profile')
        intent=interpret('查一下我的可用余额',[],[])
        if intent.intent!='account':
            raise RuntimeError('模型意图测试未通过')
        answer=generate('我现在有多少可用余额？',[],intent,AgentResponse(
            text='可用余额为12000.00元。',evidence={'available_balance':12000.00,'fictional':True}))
        print('PASS: '+settings.model+' 意图识别与证据回复生成')
        print(answer.text)
        return 0
    except HTTPException as exc:
        print(str(exc.detail),file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc),file=sys.stderr)
        return 1
    except Exception:
        print('模型连接检查失败，请检查配置及网络。',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
