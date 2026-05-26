#!/usr/bin/env python3
"""
LLM Client - 多模型支持客户端

支持 DeepSeek、阿里百炼等多个LLM提供商
"""

import json
import os
from typing import Optional, List, Dict, Any
from pathlib import Path

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("警告: openai 未安装，请先安装:")
    print("  pip install openai")


class BaseLLMClient:
    """LLM 客户端基类"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "",
        base_url: str = "",
        timeout: int = 300,
        api_key_env: str = "",
    ):
        """
        初始化 LLM 客户端

        Args:
            api_key: API Key
            model: 模型名称
            base_url: API 基础 URL
            timeout: 请求超时时间（秒）
            api_key_env: API Key 环境变量名
        """
        if not OPENAI_AVAILABLE:
            raise RuntimeError(
                "openai 库未安装。请先安装: pip install openai"
            )

        self.api_key = api_key or os.getenv(api_key_env)
        if not self.api_key:
            raise ValueError(
                f"API Key 未设置。请通过参数提供或设置环境变量 {api_key_env}"
            )

        self.model = model
        self.base_url = base_url
        self.timeout = timeout

        # 创建 OpenAI 客户端（兼容模式）
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
        )

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """
        聊天接口

        Args:
            prompt: 用户提示词
            system: 系统提示词
            temperature: 温度参数（0-1）
            max_tokens: 最大 token 数

        Returns:
            模型回复内容
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"API 调用失败: {e}")

    def chat_json(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> dict:
        """
        聊天接口，返回 JSON

        Args:
            prompt: 用户提示词
            system: 系统提示词
            temperature: 温度参数（较低以获得更稳定的 JSON）
            max_tokens: 最大 token 数

        Returns:
            解析后的 JSON 对象
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},  # 强制 JSON 格式
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            # 如果不支持 response_format，回退到普通模式
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = response.choices[0].message.content
                return self._extract_json(content)
            except Exception as e2:
                raise RuntimeError(f"API 调用失败: {e2}")

    def _extract_json(self, text: str) -> dict:
        """从文本中提取 JSON"""
        import re

        # 尝试直接解析
        try:
            return json.loads(text)
        except:
            pass

        # 尝试提取 ```json ... ``` 代码块
        pattern = r'```json\s*(.*?)\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            try:
                return json.loads(matches[0])
            except:
                pass

        # 尝试提取 {...} 内容
        pattern = r'\{.*\}'
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            for match in reversed(matches):
                try:
                    return json.loads(match)
                except:
                    continue

        raise RuntimeError(f"无法解析 JSON: {text[:100]}...")

    def health_check(self) -> bool:
        """健康检查"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=10,
            )
            return True
        except:
            return False

    def get_info(self) -> dict:
        """获取客户端信息"""
        return {
            "type": "base",
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
        }


class AliBailianClient(BaseLLMClient):
    """阿里百炼客户端"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen-max",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout: int = 300,
    ):
        """
        初始化阿里百炼客户端

        Args:
            api_key: API Key
            model: 模型名称 (qwen-max, qwen-plus, qwen-turbo)
            base_url: API 基础 URL
            timeout: 请求超时时间（秒）
        """
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            api_key_env="DASHSCOPE_API_KEY"
        )

    def get_info(self) -> dict:
        """获取客户端信息"""
        return {
            "type": "alibailian",
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
        }

    def get_models(self) -> List[dict]:
        """获取可用模型列表"""
        return [
            {"id": "qwen3-max", "name": "通义千问 3 Max", "description": "最新最强能力"},
            {"id": "qwen-max", "name": "通义千问 Max", "description": "最强能力"},
            {"id": "qwen-plus", "name": "通义千问 Plus", "description": "综合能力强"},
            {"id": "qwen-turbo", "name": "通义千问 Turbo", "description": "速度快、成本低"},
            {"id": "qwen-max-latest", "name": "通义千问 Max Latest", "description": "最新版本"},
        ]


class DeepSeekClient(BaseLLMClient):
    """DeepSeek 客户端"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "deepseek-coder",
        base_url: str = "https://api.deepseek.com",
        timeout: int = 300,
    ):
        """
        初始化 DeepSeek 客户端

        Args:
            api_key: API Key
            model: 模型名称 (deepseek-coder, deepseek-chat)
            base_url: API 基础 URL
            timeout: 请求超时时间（秒）
        """
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            api_key_env="DEEPSEEK_API_KEY"
        )

    def get_info(self) -> dict:
        """获取客户端信息"""
        return {
            "type": "deepseek",
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
        }

    def get_models(self) -> List[dict]:
        """获取可用模型列表"""
        return [
            {"id": "deepseek-coder", "name": "DeepSeek Coder", "description": "代码优化生成模型"},
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "description": "通用对话模型"},
        ]


# 创建客户端工厂函数
def create_client(provider: str, **kwargs) -> BaseLLMClient:
    """创建LLM客户端

    Args:
        provider: 提供商 ('alibailian', 'deepseek')
        **kwargs: 其他参数

    Returns:
        LLM客户端实例
    """
    if provider == "deepseek":
        return DeepSeekClient(**kwargs)
    elif provider == "alibailian":
        return AliBailianClient(**kwargs)
    else:
        raise ValueError(f"不支持的提供商: {provider}")


# 默认客户端实例（向后兼容）
_default_client = None


def get_client(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> AliBailianClient:
    """获取客户端实例（向后兼容，默认阿里百炼）"""
    global _default_client

    if _default_client is None:
        # 优先级: 参数 > .env 文件 > 环境变量 > 默认值
        api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        model = model or os.getenv("DASHSCOPE_MODEL", "qwen3-max")

        _default_client = AliBailianClient(api_key=api_key, model=model)

    return _default_client


# 便捷函数
def chat(prompt: str, system: str = "") -> str:
    """便捷的聊天函数"""
    return get_client().chat(prompt, system)


def chat_json(prompt: str, system: str = "") -> dict:
    """便捷的聊天函数（返回 JSON）"""
    return get_client().chat_json(prompt, system)