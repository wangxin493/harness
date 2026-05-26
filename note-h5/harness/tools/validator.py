#!/usr/bin/env python3
"""
代码验证器

职责：
1. 验证生成的代码是否符合规范
2. 运行 ESLint 检查
3. 运行 TypeScript 类型检查
4. 运行依赖检查
"""

import subprocess
import json
from pathlib import Path
from typing import Dict, Any, List, Optional


class CodeValidator:
    """代码验证器"""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).parent.parent
        self.scripts_dir = self.base_dir / "scripts"

    def validate_all(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        运行所有验证

        Args:
            file_path: 可选，只验证指定文件

        Returns:
            验证结果
        """
        results = {
            "passed": True,
            "checks": {},
            "errors": []
        }

        # 1. 依赖检查
        deps_result = self.check_dependencies()
        results["checks"]["dependencies"] = deps_result
        if not deps_result["passed"]:
            results["passed"] = False

        # 2. 代码质量检查
        quality_result = self.check_code_quality(file_path)
        results["checks"]["quality"] = quality_result
        if not quality_result["passed"]:
            results["passed"] = False

        # 3. TypeScript 类型检查
        type_result = self.check_types()
        results["checks"]["types"] = type_result
        if not type_result["passed"]:
            results["passed"] = False

        # 计算总分
        total_score = 0
        count = 0
        for check in results["checks"].values():
            if "score" in check:
                total_score += check["score"]
                count += 1

        if count > 0:
            results["score"] = total_score // count
        else:
            results["score"] = 0

        return results

    def check_dependencies(self) -> Dict[str, Any]:
        """检查依赖层级"""
        script_path = self.scripts_dir / "lint-deps.py"

        if not script_path.exists():
            return {"passed": True, "message": "依赖检查脚本不存在，跳过"}

        try:
            result = subprocess.run(
                ["python3", str(script_path)],
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=30
            )

            # 解析输出
            violations = []
            for line in result.stdout.split("\n"):
                if "❌" in line or "错误" in line:
                    violations.append(line.strip())

            return {
                "passed": len(violations) == 0,
                "violations": violations,
                "message": result.stdout
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "message": "依赖检查超时"}
        except Exception as e:
            return {"passed": False, "message": str(e)}

    def check_code_quality(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """检查代码质量"""
        script_path = self.scripts_dir / "lint-quality.py"

        if not script_path.exists():
            return {"passed": True, "message": "代码质量检查脚本不存在，跳过"}

        try:
            cmd = ["python3", str(script_path)]
            if file_path:
                cmd.extend(["--file", file_path])

            result = subprocess.run(
                cmd,
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            # 解析输出，提取分数和问题
            score = 0
            issues = []

            for line in result.stdout.split("\n"):
                if "代码质量评分" in line:
                    # 提取分数
                    parts = line.split(":")
                    if len(parts) > 1:
                        try:
                            score = int(parts[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass

            return {
                "passed": score >= 70,
                "score": score,
                "message": result.stdout
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "score": 0, "message": "代码质量检查超时"}
        except Exception as e:
            return {"passed": False, "score": 0, "message": str(e)}

    def check_types(self) -> Dict[str, Any]:
        """检查 TypeScript 类型"""
        # 使用项目的 TypeScript 编译器
        tsconfig_path = self.base_dir / "tsconfig.json"
        if not tsconfig_path.exists():
            return {"passed": True, "message": "tsconfig.json 不存在，跳过类型检查"}

        try:
            # 检查 node_modules 是否存在
            node_modules = self.base_dir / "node_modules" / ".bin" / "tsc"
            if not node_modules.exists():
                return {"passed": True, "message": "TypeScript 未安装，跳过类型检查"}

            result = subprocess.run(
                ["./node_modules/.bin/tsc", "--noEmit"],
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            has_errors = result.returncode != 0

            return {
                "passed": not has_errors,
                "message": result.stdout if has_errors else "类型检查通过"
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "message": "类型检查超时"}
        except Exception as e:
            return {"passed": False, "message": str(e)}

    def fix_issues(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """尝试自动修复问题"""
        results = {
            "fixed": [],
            "failed": []
        }

        # 运行 ESLint 自动修复
        try:
            cmd = ["npm", "run", "lint:fix"]
            result = subprocess.run(
                cmd,
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                results["fixed"].append("ESLint 格式问题已自动修复")
            else:
                results["failed"].append("ESLint 自动修复失败")

        except Exception as e:
            results["failed"].append(f"自动修复失败: {e}")

        return results
