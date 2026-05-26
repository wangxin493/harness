#!/usr/bin/env python3
"""
AutoExecutor - 自动执行器

职责：
1. 自动按执行计划依次调用 Agent
2. Reviewer 不通过时自动重试
3. 处理失败和回滚
4. 记录执行轨迹

注意：实际的 Agent 逻辑（AI 决策）通过回调函数注入，由阿里百炼 API 处理
"""

import json
import sys
import re
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness import TaskStatus
from core.orchestrator import Orchestrator, ExecutionPlan
from tools.utils import extract_json_from_text, write_file, validate_file_path
from tools.code_scanner import CodeScanner


def _normalize_file_path(file_path: str, base_dir: Optional[Path] = None) -> str:
    """
    规范化文件路径，用于比较
    - 转换为相对路径
    - 统一斜杠方向
    - 移除多余斜杠
    """
    if not file_path:
        return ""

    # 统一为正斜杠
    file_path = file_path.replace("\\", "/")

    # 如果是绝对路径，尝试转为相对路径
    if base_dir and file_path.startswith(str(base_dir)):
        file_path = file_path[len(str(base_dir)):].lstrip("/")

    # 移除 leading/trailing 斜杠
    file_path = file_path.strip("/")

    return file_path


def _file_paths_match(path1: str, path2: str, base_dir: Optional[Path] = None) -> bool:
    """判断两个文件路径是否指向同一文件"""
    norm1 = _normalize_file_path(path1, base_dir)
    norm2 = _normalize_file_path(path2, base_dir)
    return norm1 == norm2


class AutoExecutor:
    """自动执行器"""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        planner_handler: Optional[Callable] = None,
        coder_handler: Optional[Callable] = None,
        reviewer_handler: Optional[Callable] = None
    ):
        self.base_dir = base_dir or Path(__file__).parent.parent.parent
        self.orchestrator = Orchestrator(self.base_dir)

        # 初始化代码扫描器
        self.code_scanner = CodeScanner(self.base_dir)

        # LLM 处理函数（通过外部注入，使用阿里百炼 API）
        self.planner_handler = planner_handler
        self.coder_handler = coder_handler
        self.reviewer_handler = reviewer_handler

        # 配置
        self.max_coder_retries = 3
        self.max_reviewer_retries = 2  # 最大重试次数

        # 代码上下文缓存
        self.code_context = None

        # 历史问题记录（用于跟踪已经发现的问题）
        self.historical_issues: List[Dict[str, Any]] = []

    def _ensure_reviewer_steps(self, plan: ExecutionPlan) -> None:
        """确保每个 Coder 步骤后面都有 Reviewer 步骤

        如果 Planner 没有生成 Reviewer 步骤，自动在每个 Coder 步骤后插入一个。
        这是防止代码跳过审查的核心机制。
        """
        new_steps = []
        for i, step in enumerate(plan.steps):
            new_steps.append(step)
            # 在每个 Coder 步骤后面插入 Reviewer
            if step.get("agent") == "coder":
                reviewer_step = {
                    "step": len(new_steps) + 1,
                    "agent": "reviewer",
                    "description": f"审查代码: {step.get('description', '未知步骤')}",
                    "target_file": step.get("target_file", ""),
                    "requirements": [
                        "验证类型一致性：检查所有字段名是否与 @/types 中的定义一致",
                        "验证 import 路径是否正确",
                        "验证接口契约：Hook 返回的方法是否与 Page 调用的一致",
                        "检查是否有任何 TypeScript 编译错误"
                    ]
                }
                new_steps.append(reviewer_step)

        plan.steps = new_steps
        plan.estimated_steps = len(new_steps)

        # 重新编号
        for i, step in enumerate(plan.steps, 1):
            step["step"] = i

        print(f"\n  [AutoExecutor] ⚠️ 自动注入了 {len(new_steps) - len(plan.steps) // 2} 个 Reviewer 步骤")
        print(f"  [AutoExecutor] 最终执行计划共 {len(plan.steps)} 个步骤")

    def execute_task(self, user_request: str, priority: str = "P2") -> Dict[str, Any]:
        """
        执行完整任务流程

        Args:
            user_request: 用户需求描述
            priority: 任务优先级

        Returns:
            执行结果字典
        """
        # 创建任务
        task = self.orchestrator.create_task(user_request, priority)
        trace_id = self.orchestrator.start_execution(task.task_id, user_request)

        result = {
            "task_id": task.task_id,
            "trace_id": trace_id,
            "status": "started",
            "steps": [],
            "errors": []
        }

        try:
            # 0. 代码扫描阶段（新增）
            print(f"\n[CodeScanner] 扫描现有代码...")
            self.code_context = self.code_scanner.scan_all()
            print(f"  [CodeScanner] ✓ 扫描完成")
            print(f"  [CodeScanner] 发现 {self.code_context['summary']['total_components']} 个组件")
            print(f"  [CodeScanner] 发现 {self.code_context['summary']['total_hooks']} 个 Hooks")
            print(f"  [CodeScanner] 发现 {self.code_context['summary']['total_apis']} 个 APIs")
            print(f"  [CodeScanner] 发现 {self.code_context['summary'].get('total_types', 0)} 个类型定义")

            # 扩展 code_context 包含详细信息
            self._enrich_code_context()

            # 检测是否有类似功能（新增）
            similar_functionality = self.code_scanner.find_similar_functionality(user_request)
            if similar_functionality:
                print(f"  [CodeScanner] ⚠ 检测到可能存在的类似功能:")
                for sim in similar_functionality:
                    print(f"    - 关键词: {sim['keyword']}, 文件: {sim['file']}, 模式: {sim['pattern']}")

            result["code_context"] = self.code_context
            result["similar_functionality"] = similar_functionality

            # 1. Planner Agent 生成执行计划
            plan = self._execute_planner(task.task_id, trace_id, user_request, self.code_context)
            if not plan:
                raise RuntimeError("Planner 未能生成执行计划")

            result["plan"] = plan.to_dict()

            # 2. 按计划执行每个步骤
            # === FIX 1: 自动注入 Reviewer 步骤（如果 Planner 没有生成）===
            self._ensure_reviewer_steps(plan)

            # === NEW: P1-1 文件路径校验 ===
            path_errors = self._validate_plan_paths(plan)
            if path_errors:
                print(f"\n[AutoExecutor] ⚠️ 发现 {len(path_errors)} 个路径问题:")
                for err in path_errors:
                    print(f"  - {err.get('message', '')}")
                # 不阻止执行，但记录问题
                result.setdefault("warnings", []).extend(path_errors)

            # === NEW: P2 预生成契约文件 ===
            self._generate_contracts_file()

            # 跟踪是否有任何步骤失败
            any_step_failed = False

            for step in plan.steps:
                step_result = self._execute_step_with_retry(
                    task.task_id,
                    trace_id,
                    step,
                    result
                )
                result["steps"].append(step_result)

                # 如果步骤失败，记录错误但继续执行后续步骤
                if not step_result["success"]:
                    error_msg = f"步骤执行失败: {step.get('description')}"
                    result["errors"].append(error_msg)
                    print(f"\n[AutoExecutor] ⚠ {error_msg}，继续执行后续步骤...")
                    any_step_failed = True

                # 增量代码扫描：每步成功后重新扫描，让后续步骤感知已有代码
                if step.get("agent") == "coder" and step_result["success"]:
                    self._rescan_code()

            # 3. 任务完成
            success = not any_step_failed

            # 最终编译验证：如果步骤都成功但编译失败，状态应为 partial_failure
            if success:
                final_build_ok, _ = self._verify_build()
                if not final_build_ok:
                    success = False
                    result["errors"].append("最终编译验证失败")

            self.orchestrator.complete_execution(
                task.task_id,
                trace_id,
                success=success,
                summary=f"{'成功完成' if success else '部分步骤失败'}: {user_request}"
            )
            result["status"] = "completed" if success else "partial_failure"

            # === 反哺：保存执行经验 ===
            self._save_lessons(user_request, result)

        except Exception as e:
            # 任务失败
            self.orchestrator.complete_execution(
                task.task_id,
                trace_id,
                success=False,
                summary=f"执行失败: {str(e)}"
            )
            result["status"] = "failed"
            result["errors"].append(str(e))

            # === 反哺：保存失败经验 ===
            self._save_lessons(user_request, result)

        return result

    def _save_lessons(self, task_description: str, result: Dict[str, Any]) -> None:
        """将本次执行的经验保存到 memory 目录，供下次执行反哺"""
        try:
            errors = result.get("errors", [])
            if not errors and not self.historical_issues:
                return  # 没有值得记录的经验

            filename = self.orchestrator.save_execution_lessons(
                task_description=task_description,
                errors=errors,
                issues=self.historical_issues,
                status=result.get("status", "unknown")
            )
            if filename:
                print(f"\n[Memory] ✓ 经验已保存: {filename}")
        except Exception as e:
            print(f"\n[Memory] ⚠ 保存经验失败: {e}")

    def _execute_planner(
        self,
        task_id: str,
        trace_id: str,
        user_request: str,
        code_context: Dict[str, Any] = None
    ) -> Optional[ExecutionPlan]:
        """执行 Planner Agent"""
        print(f"\n[Planner] 分析需求: {user_request}")

        # 获取 Planner 提示词
        system_prompt = self.orchestrator.get_planner_prompt()

        # 调用 LLM 处理（通过注入的 handler）
        if self.planner_handler:
            plan_output = self.planner_handler(
                user_request=user_request,
                system_prompt=system_prompt,
                code_context=code_context
            )
        else:
            # 模拟返回（用于测试）
            print("  [警告] 未注入 planner_handler，使用模拟输出")
            plan_output = json.dumps({
                "task_description": user_request,
                "estimated_steps": 2,
                "steps": [
                    {
                        "step": 1,
                        "agent": "coder",
                        "description": "示例步骤 1",
                        "target_file": "src/example.tsx"
                    },
                    {
                        "step": 2,
                        "agent": "reviewer",
                        "description": "审查代码"
                    }
                ]
            })

        # 提取 JSON
        plan_data = extract_json_from_text(plan_output)
        if not plan_data:
            raise RuntimeError("无法解析 Planner 输出")

        plan = ExecutionPlan(plan_data)

        # 验证计划是否包含必要的依赖创建步骤
        validated_plan = self._validate_plan(plan, code_context)
        if validated_plan != plan:
            print(f"  [Planner] ⚠️ 计划验证通过后已调整步骤数量")

        # 调试信息
        print(f"  [Planner] 生成计划: {validated_plan.estimated_steps} 个步骤")
        for i, step in enumerate(validated_plan.steps, 1):
            print(f"    {i}. [{step.get('agent')}] {step.get('description')}")

        # 记录结果
        self.orchestrator.record_planner_result(task_id, trace_id, validated_plan.to_dict())

        return validated_plan

    def _validate_plan(self, plan: ExecutionPlan, code_context: Dict[str, Any]) -> ExecutionPlan:
        """验证并修正执行计划，确保包含必要的依赖创建步骤

        Args:
            plan: 原始执行计划
            code_context: 代码上下文信息

        Returns:
            验证后的执行计划
        """
        # 检查关键模块是否存在
        types_file_exists = (self.base_dir / "src" / "types" / "index.ts").exists()

        # 检查是否已有任意 api 服务文件
        api_dir = self.base_dir / "src" / "api"
        any_service_exists = api_dir.exists() and any(api_dir.glob("*.ts"))

        # 扫描计划的步骤，检查是否创建了类型和 API 服务
        has_types_step = any(
            'types' in step.get('target_file', '').lower()
            for step in plan.steps if step.get('agent') == 'coder'
        )
        has_api_step = any(
            'api' in step.get('target_file', '').lower()
            for step in plan.steps if step.get('agent') == 'coder'
        )

        # 如果需要但缺少类型定义，插入通用类型创建步骤
        if not types_file_exists and not has_types_step:
            print(f"  [Planner] ⚠️ 检测到缺少 @/types 模块，自动添加创建类型步骤")
            types_step = {
                "step": 1,
                "agent": "coder",
                "description": "创建类型定义",
                "target_file": "src/types/index.ts",
                "requirements": [
                    "根据用户需求定义所有必要的 TypeScript 接口和类型",
                    "确保类型覆盖用户需求中涉及的所有实体",
                    "使用 interface 或 type 定义"
                ]
            }
            self._insert_step_at_position(plan, types_step, 0)

        # 如果需要但缺少 API 服务，插入通用服务创建步骤
        if not any_service_exists and not has_api_step:
            print(f"  [Planner] ⚠️ 检测到缺少 API 服务，自动添加创建服务步骤")
            service_step = {
                "step": 1,
                "agent": "coder",
                "description": "创建 API 服务",
                "target_file": "src/api/noteService.ts",
                "requirements": [
                    "从 @/types 导入类型",
                    "导出服务对象，提供完整的 CRUD 方法",
                    "根据用户需求实现具体的业务逻辑方法"
                ]
            }
            # 找到正确的插入位置（在类型定义之后，Hook 之前）
            insert_pos = 1 if has_types_step or (not types_file_exists) else 0
            self._insert_step_at_position(plan, service_step, insert_pos)

        # 将 entry-point 文件步骤移到前面：类型创建之后、其他步骤之前
        self._reorder_entry_points(plan)

        # 更新步骤编号
        for i, step in enumerate(plan.steps, 1):
            step['step'] = i
            plan.estimated_steps = len(plan.steps)

        return plan

    def _insert_step_at_position(self, plan: ExecutionPlan, new_step: Dict[str, Any], position: int):
        """在指定位置插入新步骤

        Args:
            plan: 执行计划
            new_step: 要插入的新步骤
            position: 插入位置（0-based 索引）
        """
        plan.steps.insert(position, new_step)

    def _reorder_entry_points(self, plan: ExecutionPlan) -> None:
        """将 index.tsx 和 App.tsx 步骤移到类型创建之后、其他步骤之前

        这样可以尽早验证编译，避免到最后才发现 entry-point 缺失。
        """
        entry_files = {"src/index.tsx", "src/App.tsx"}
        entry_steps = []
        other_steps = []

        for step in plan.steps:
            target = step.get("target_file", "")
            if target in entry_files:
                entry_steps.append(step)
            else:
                other_steps.append(step)

        if not entry_steps:
            return  # 没有 entry-point 步骤，无需重排

        # 找到类型创建步骤的位置（types/index.ts）
        types_pos = -1
        for i, step in enumerate(other_steps):
            target = step.get("target_file", "")
            if target == "src/types/index.ts":
                types_pos = i + 1  # 插入到类型步骤之后
                break

        insert_pos = max(types_pos, 0) if types_pos >= 0 else 0

        # 将 entry-point 步骤插入到目标位置
        for entry_step in reversed(entry_steps):
            other_steps.insert(insert_pos, entry_step)

        plan.steps = other_steps
        print(f"  [Planner] ⚠️ 已将 {len(entry_steps)} 个 entry-point 步骤移到类型创建之后")

    def _get_issues_for_file(self, issues: List[Dict[str, Any]], target_file: str) -> List[Dict[str, Any]]:
        """过滤出与指定文件相关的问题"""
        return [
            i for i in issues
            if _file_paths_match(i.get("file", ""), target_file, self.base_dir)
            or not i.get("file")  # 全局问题也带上
        ]

    def _detect_dependency_breakage(
        self,
        issues: List[Dict[str, Any]],
        failed_target: Dict[str, Any],
        coder_steps: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """检测依赖链断裂：Hook 调用了 Service 中不存在的方法（TS2339）

        当编译错误包含 TS2339（Property does not exist）时，
        找到对应的 Service 文件并返回，让 regenerate 去回溯修复它。
        """
        import re as regex_module

        hook_target_file = failed_target.get("target_file", "")
        if not hook_target_file or "hook" not in hook_target_file.lower():
            return None  # 不是 hook 文件，无需回溯

        # 检查是否存在 TS2339 错误
        has_ts2339 = any(
            "TS2339" in i.get("message", "") or "does not exist" in i.get("message", "")
            for i in issues
        )
        if not has_ts2339:
            return None

        # 从错误信息中提取缺失的属性名
        missing_props = set()
        for issue in issues:
            msg = issue.get("message", "")
            match = regex_module.search(r"Property '(\w+)' does not exist", msg)
            if match:
                missing_props.add(match.group(1))

        if not missing_props:
            return None

        # 尝试读取 hook 文件，找到它 import 的 service 文件
        hook_path = self.base_dir / hook_target_file
        service_target = None

        if hook_path.exists():
            content = hook_path.read_text(encoding="utf-8")
            # 查找 import 语句中的 service 文件
            service_match = regex_module.findall(
                r'import\s+\{[^}]*\}\s+from\s+["\']@/api/(\w+)["\']',
                content
            )
            for service_name in service_match:
                service_file = f"src/api/{service_name}.ts"
                # 检查这个 service 是否在 coder_steps 中被创建过
                for coder_step in coder_steps:
                    if _file_paths_match(coder_step.get("target_file", ""), service_file, self.base_dir):
                        service_target = coder_step
                        break
                if service_target:
                    break

        if service_target:
            print(f"  [Backtrack] 检测到 TS2339，缺失属性: {missing_props}")
            print(f"  [Backtrack] 回溯目标: {service_target['target_file']}")
            return service_target

        return None

    def _build_backtrack_issues(
        self,
        original_issues: List[Dict[str, Any]],
        backtrack_target: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """为回溯修复构建增强版 issue 列表

        读取 service 文件的真实导出方法签名，构造精确的 issue 告诉 Coder 该加什么方法。
        """
        import re as regex_module

        target_file = backtrack_target.get("target_file", "")
        service_path = self.base_dir / target_file

        # 读取 service 文件，提取所有导出方法及其签名
        existing_methods = {}
        if service_path.exists():
            content = service_path.read_text(encoding="utf-8")
            # 匹配 export const xxxService = { method1, method2, ... }
            method_matches = regex_module.findall(
                r'(?:async\s+)?(\w+)\s*[\(:]',
                content
            )
            existing_methods = set(method_matches)

        # 从原始 issues 中提取缺失的属性名
        missing_props = set()
        for issue in original_issues:
            msg = issue.get("message", "")
            match = regex_module.search(r"Property '(\w+)' does not exist", msg)
            if match:
                missing_props.add(match.group(1))

        if not missing_props:
            return original_issues

        # 构建增强 issue
        enhanced = [{
            "severity": "error",
            "category": "dependency-backtrack",
            "file": target_file,
            "message": f"依赖断裂：以下方法在 service 中不存在，需要通过 Hook 调用：{', '.join(sorted(missing_props))}",
            "suggestion": (
                f"请在 {target_file} 文件中添加以下方法：\n"
                + "\n".join(f"  - async {prop}(): Promise<any>" for prop in sorted(missing_props))
                + f"\n\n当前已有的方法：{', '.join(sorted(existing_methods)) if existing_methods else '无'}"
                + "\n\n确保新方法被包含在导出的 service 对象中。"
            )
        }]
        return enhanced

    def _filter_new_issues(self, current_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """过滤出新问题（不在历史记录中的问题）

        Args:
            current_issues: 当前审查发现的问题

        Returns:
            新问题列表
        """
        if not self.historical_issues:
            return current_issues

        new_issues = []
        for issue in current_issues:
            # 检查这个问题是否已经在历史记录中
            is_duplicate = False
            for historical_issue in self.historical_issues:
                if self._issues_match([historical_issue], [issue]):
                    is_duplicate = True
                    break

            if not is_duplicate:
                new_issues.append(issue)

        return new_issues

    def _issues_match(self, issues1: List[Dict], issues2: List[Dict]) -> bool:
        """检查两个问题列表是否相同

        Args:
            issues1: 第一个问题列表
            issues2: 第二个问题列表

        Returns:
            True 如果问题相同（数量和内容都相同）
        """
        if len(issues1) != len(issues2):
            return False

        # 构建问题哈希集合
        def issue_hash(issue: Dict) -> str:
            # 使用问题的主要特征来比较（包含文件路径）
            file_path = _normalize_file_path(issue.get('file', ''), self.base_dir)
            return f"{issue.get('severity')}|{issue.get('category', '')}|{file_path}|{issue.get('message', '')}"

        hash1 = {issue_hash(i) for i in issues1}
        hash2 = {issue_hash(i) for i in issues2}

        return hash1 == hash2

    def _execute_step_with_retry(
        self,
        task_id: str,
        trace_id: str,
        step: Dict[str, Any],
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        执行单个步骤（带重试机制）

        当 Reviewer 不通过时，自动将问题反馈给 Coder 重新生成
        """
        agent = step.get("agent")

        # 非 Reviewer 步骤直接执行
        if agent != "reviewer":
            return self._execute_step(task_id, trace_id, step, result)

        # Reviewer 步骤：需要先执行 Coder，然后 Reviewer，循环重试
        description = step.get("description", "审查代码")

        print(f"\n[Review Loop] 开始代码审查循环")

        # 获取需要审查的 Coder 步骤（在这个 Reviewer 之前的 Coder 步骤）
        coder_steps_to_review = self._get_coder_steps_before_reviewer(result, step)

        if not coder_steps_to_review:
            print("  [Review Loop] 没有需要审查的代码")
            return {
                "step": step.get("step"),
                "agent": "reviewer",
                "description": description,
                "success": True,
                "retries": 0,
                "output": None
            }

        # 开始审查循环
        max_retries = self.max_reviewer_retries
        retry_count = 0
        previous_issues = None  # 记录上一次的问题，用于检测相同问题

        while retry_count <= max_retries:
            retry_count += 1
            print(f"\n[Review Loop] 第 {retry_count} 次审查")

            # 执行 Reviewer
            review_result = self._execute_reviewer_step(
                task_id, trace_id, step, {
                    "step": step.get("step"),
                    "agent": "reviewer",
                    "description": description,
                    "success": False,
                    "retries": retry_count - 1,
                    "output": None
                }
            )

            # 审查不通过
            review_data = review_result.get("output", {})
            current_issues = review_data.get("issues", [])

            # 检查是否只有 warning 和 info 级别的问题
            error_issues = [i for i in current_issues if i.get('severity') == 'error']

            # 只有 error 级别问题时，检查编译
            has_only_warnings_infos = not error_issues and len(current_issues) > 0
            no_errors_at_all = not error_issues and len(current_issues) == 0

            if review_result["success"] or has_only_warnings_infos or no_errors_at_all:
                # 审查通过，进行编译验证
                print(f"\n[Review Loop] ✓ 审查通过，进行编译验证...")

                build_success, build_errors = self._verify_build()

                if build_success:
                    print(f"[Review Loop] ✓ 编译成功")
                    # 编译通过时，即使 reviewer 有 warning 也不阻止通过
                    review_result["success"] = True
                    if has_only_warnings_infos:
                        print(f"[Review Loop] 当前问题: warning {len([i for i in current_issues if i.get('severity') == 'warning'])} 个, info {len([i for i in current_issues if i.get('severity') == 'info'])} 个 (已忽略)")
                    return review_result
                else:
                    # 编译失败，需要重试
                    print(f"[Review Loop] ✗ 编译失败，需要修复")
                    print(f"[Review Loop] 编译错误数量: {len(build_errors)}")

                    # 将编译错误转换为 Reviewer 问题格式
                    # 尝试从错误信息中提取文件名
                    for error in build_errors[:10]:  # 限制显示数量
                        error_msg = error.get("message", "") if isinstance(error, dict) else error
                        # 尝试提取文件路径
                        file_path = "编译错误"
                        if "src/" in error_msg:
                            import re
                            match = re.search(r'src/[^\s:]+\.(tsx?|ts)', error_msg)
                            if match:
                                file_path = match.group()

                        self.historical_issues.append({
                            "severity": "error",
                            "category": "typescript",
                            "message": error_msg,
                            "suggestion": "修复 TypeScript 类型错误",
                            "file": file_path
                        })

                    # 显示编译错误
                    print(f"[Review Loop] 编译错误:")
                    for idx, error in enumerate(build_errors[:5], 1):
                        print(f"  {idx}. {error}")

                    if retry_count >= max_retries:
                        print(f"\n[Review Loop] ✗ 达到最大重试次数 {max_retries}，放弃")
                        print(f"[Review Loop] 累计发现 {len(self.historical_issues)} 个问题")
                        return review_result

                    # 继续重试
                    print(f"\n[Review Loop] 准备修复编译错误并重新生成代码...")
                    continue

            # 过滤掉已经在历史记录中的问题（只保留新问题）
            new_issues = self._filter_new_issues(current_issues)

            # 将新问题添加到历史记录
            self.historical_issues.extend(new_issues)

            # === FIX: 如果编译通过且没有新的严重问题（TypeScript 错误），通过审查 ===
            # 只有 TypeScript 类型错误才算阻塞性问题，warning 级别的不阻止通过
            has_ts_errors = any(
                issue.get("severity") == "error" and issue.get("category") in ["type-safety", "type-consistency"]
                for issue in new_issues
            )

            # 重新编译验证
            verify_success, verify_errors = self._verify_build()

            # 编译成功 = 最终通过（编译是真理来源）
            if verify_success:
                print(f"\n[Review Loop] ✓ 编译成功，覆盖 Reviewer 判断，审查通过")
                review_result["success"] = True
                review_result["output"] = {
                    "passed": True,
                    "summary": "编译通过",
                    "issues": self.historical_issues,
                    "score": review_data.get("score", 80),
                }
                return review_result

            # 编译失败 = 不通过，无论 Reviewer 说什么
            print(f"\n[Review Loop] ✗ 编译失败 ({len(verify_errors)} errors)，审查不通过")
            review_result["success"] = False

            # 如果编译仍然失败，添加编译错误到 new_issues 并继续重试
            if not verify_success:
                # 提取文件路径并添加编译错误到 new_issues
                import re  # 确保 re 模块可用
                for error in verify_errors[:10]:
                    error_msg = error if isinstance(error, str) else error.get("message", "")
                    file_path = "编译错误"
                    # 尝试从完整路径中提取 src/... 相对路径
                    match = re.search(r'(?:src/|(?:/[^/]+){3}src/)([^\s:]+\.(tsx?|ts))', error_msg)
                    if match:
                        file_path = match.group(1) if match.lastindex and match.group(1) else match.group()
                    else:
                        # 尝试直接匹配 src/...
                        match = re.search(r'src/[^\s:]+\.(tsx?|ts)', error_msg)
                        if match:
                            file_path = match.group()

                    compile_error = {
                        "severity": "error",
                        "category": "typescript",
                        "message": error_msg,
                        "suggestion": "修复 TypeScript 类型错误",
                        "file": file_path
                    }
                    # 检查是否已存在相同的编译错误（使用路径匹配）
                    is_duplicate = any(
                        _file_paths_match(e.get("file", ""), file_path, self.base_dir)
                        and e.get("message", "") == error_msg
                        for e in new_issues
                    )
                    if not is_duplicate:
                        new_issues.append(compile_error)
                    if not any(_file_paths_match(e.get("file", ""), file_path, self.base_dir) and e.get("message", "") == error_msg for e in self.historical_issues):
                        self.historical_issues.append(compile_error)

                print(f"\n[Review Loop] 编译仍失败，添加编译错误到问题列表")

            # 如果没有新问题且之前也有问题，说明卡住了
            if not new_issues and previous_issues:
                print(f"\n[Review Loop] ⚠ 没有发现新问题，且问题重复，停止重试")
                print(f"[Review Loop] 问题: {previous_issues[0].get('message', '') if previous_issues else '无'}")
                print(f"[Review Loop] 建议: 检查代码是否存在根本问题或 Reviewer 的判断标准")

                # 记录结果，返回失败
                review_result["output"] = {
                    "passed": False,
                    "summary": "没有发现新问题，问题重复出现",
                    "issues": self.historical_issues,
                    "score": review_data.get("score", 0),
                    "suggestions": [
                        "问题重复出现，可能是代码根本性问题或 Reviewer 标准问题",
                        "建议人工检查并修复后重试"
                    ]
                }
                return review_result

            # 更新 previous_issues
            previous_issues = current_issues

            # 如果是最后一次重试，返回失败
            if retry_count >= max_retries:
                print(f"\n[Review Loop] ✗ 达到最大重试次数 {max_retries}，放弃")
                print(f"[Review Loop] 累计发现 {len(self.historical_issues)} 个问题")
                return review_result

            # 显示当前发现的新问题
            print(f"\n[Review Loop] 审查未通过，本次发现 {len(new_issues)} 个新问题")
            print(f"[Review Loop] 累计历史问题: {len(self.historical_issues)} 个")
            print(f"[Review Loop] 本次问题如下:")

            for idx, issue in enumerate(new_issues):
                print(f"  - [{issue.get('severity', 'info')}] {issue.get('message', '')}")

            print(f"\n[Review Loop] 准备重新生成代码...")

            # === FIX: 根据 reviewer 步骤的 target_file 找到对应的 coder 步骤 ===
            reviewer_target = step.get("target_file", "")
            failed_target = None

            if reviewer_target:
                # 优先：用 reviewer 的 target_file 匹配对应的 coder 步骤
                for coder_step in reversed(coder_steps_to_review):
                    if _file_paths_match(coder_step.get("target_file", ""), reviewer_target, self.base_dir):
                        failed_target = coder_step
                        break

            if not failed_target:
                # 其次：用 issue 中的 file 字段匹配（从最后一个 coder step 往前找）
                for coder_step in reversed(coder_steps_to_review):
                    if coder_step.get("target_file"):
                        for issue in new_issues:
                            issue_file = issue.get("file", "")
                            if issue_file and _file_paths_match(issue_file, coder_step["target_file"], self.base_dir):
                                failed_target = coder_step
                                break
                    if failed_target:
                        break

            if not failed_target and coder_steps_to_review:
                # 最后兜底：取最后一个有 target_file 的 coder 步骤
                for coder_step in reversed(coder_steps_to_review):
                    if coder_step.get("target_file"):
                        failed_target = coder_step
                        break

            if failed_target:
                # === FIX 2: 依赖链断裂自动回溯 ===
                # 检测编译错误中的 TS2339（Property does not exist），
                # 这说明 Hook 调用了 Service 中不存在的方法，需要回修 Service 文件
                backtrack_target = self._detect_dependency_breakage(
                    new_issues, failed_target, coder_steps_to_review
                )

                if backtrack_target:
                    print(f"\n[Regenerate] ⚠ 检测到依赖链断裂，回溯修复: {backtrack_target['target_file']}")
                    # 构建增强版 issue：包含 service 文件的真实方法签名
                    enhanced_issues = self._build_backtrack_issues(
                        new_issues, backtrack_target
                    )
                    self._regenerate_single_file(
                        task_id, trace_id, backtrack_target, enhanced_issues
                    )

                # === FIX 3: 只传当前步骤的新问题，不传历史累积问题 ===
                print(f"\n[Regenerate] 重新生成失败文件: {failed_target['target_file']}")
                current_file_issues = self._get_issues_for_file(new_issues, failed_target["target_file"])
                self._regenerate_single_file(
                    task_id, trace_id, failed_target, current_file_issues
                )
            else:
                print(f"\n[Regenerate] 未找到问题对应的文件，重新生成所有有问题的文件")
                self._regenerate_code_with_feedback(
                    task_id, trace_id, coder_steps_to_review, new_issues
                )

        # 如果循环结束而没有返回，说明达到了最大重试次数
        print(f"\n[Review Loop] ✗ 达到最大重试次数 {max_retries}，放弃")
        print(f"[Review Loop] 累计发现 {len(self.historical_issues)} 个问题")
        return review_result

    def _verify_build(self) -> tuple[bool, list[str]]:
        """验证代码是否能够成功编译

        Returns:
            (success: bool, errors: list[str]) — 错误消息包含文件路径和具体描述
        """
        import subprocess
        import re as regex_module

        # 查找 package.json 确定构建命令
        package_json = self.base_dir / "package.json"
        if not package_json.exists():
            # 没有 package.json，假设编译成功
            print("  [Build] 未找到 package.json，跳过编译验证")
            return True, []

        build_errors = []

        # 方法1: 先尝试 webpack build
        try:
            print(f"  [Build] 执行命令: npm run build")
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=60
            )
            output = result.stdout + result.stderr

            if result.returncode == 0 and 'compiled' in output.lower():
                print(f"  [Build] ✓ 编译成功")
                return True, []

            # 编译失败 — 提取具体错误信息
            errors = self._parse_webpack_errors(output)
            if errors:
                for err in errors:
                    build_errors.append(err)
                    print(f"  [Build]   → {err[:120]}")

            # 方法2: 同时也运行 tsc 获取类型错误
            try:
                tsc_result = subprocess.run(
                    ["npx", "tsc", "--noEmit"],
                    cwd=self.base_dir,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                tsc_output = tsc_result.stdout + tsc_result.stderr
                if tsc_result.returncode != 0:
                    tsc_errors = self._parse_tsc_errors(tsc_output)
                    for err in tsc_errors:
                        if err not in build_errors:
                            build_errors.append(err)
                            print(f"  [TSC]    → {err[:120]}")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            if build_errors:
                print(f"  [Build] ✗ 编译失败，发现 {len(build_errors)} 个错误")
                return False, build_errors

        except subprocess.TimeoutExpired:
            build_errors.append("编译超时 (npm run build)")
        except FileNotFoundError:
            build_errors.append("npm 未找到")
        except Exception as e:
            build_errors.append(f"编译系统错误: {e}")

        # 无法判断，假设成功
        if not build_errors:
            print("  [Build] ⚠ 无法验证编译，假设成功")
            return True, []
        return False, build_errors

    def _parse_webpack_errors(self, output: str) -> list[str]:
        """解析 webpack 编译输出，提取可读的错误信息"""
        import re as regex_module
        errors = []

        # 匹配 webpack 的 "Module not found" 错误
        module_not_found = regex_module.findall(
            r"Module not found: Error: Can't resolve '([^']+)' in '([^']+)'",
            output
        )
        for missing_module, location in module_not_found:
            errors.append(f"缺少模块: '{missing_module}' (在 {location} 中被引用)")

        # 匹配 "Module not found" 的另一种格式
        for line in output.split('\n'):
            stripped = line.strip()
            # 跳过无意义的行
            if not stripped or stripped.startswith('Field') or stripped.startswith('using') or stripped.startswith('as directory'):
                continue
            if 'Module not found' in stripped and not any(stripped in e for e in errors):
                # 提取核心错误信息
                errors.append(stripped)

        # 匹配其他 ERROR 行
        for line in output.split('\n'):
            stripped = line.strip()
            if 'ERROR in' in stripped:
                # 尝试提取更具体的错误
                parts = stripped.split('ERROR in')
                context = parts[0].strip() if len(parts) > 0 else ""
                error_msg = parts[1].strip() if len(parts) > 1 else "未知错误"
                errors.append(f"{error_msg} ({context})" if context else error_msg)

        # 如果没有提取到具体错误，返回关键行的摘要
        if not errors:
            key_lines = [l.strip() for l in output.split('\n')
                        if l.strip() and ('error' in l.lower() or 'fail' in l.lower())][:5]
            if key_lines:
                errors = key_lines
            else:
                errors = ["编译失败，但无法提取具体错误信息"]

        return errors

    def _parse_tsc_errors(self, output: str) -> list[str]:
        """解析 tsc --noEmit 输出，提取类型错误"""
        import re as regex_module
        errors = []

        # tsc 格式: file.ts(line,col): error TS1234: message
        for line in output.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            match = regex_module.match(r'(.+?)\((\d+),(\d+)\):\s*(error\s+TS\d+:.*)', stripped)
            if match:
                errors.append(f"{match.group(1)}:{match.group(2)} — {match.group(4)}")
            elif 'error TS' in stripped.lower():
                errors.append(stripped)

        if not errors and output.strip():
            # 取最后几行作为错误摘要
            lines = [l.strip() for l in output.split('\n') if l.strip()]
            errors = lines[-5:] if len(lines) > 5 else lines

        return errors

    def _get_coder_steps_before_reviewer(
        self,
        result: Dict[str, Any],
        reviewer_step: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        获取在这个 Reviewer 之前的所有 Coder 步骤
        """
        reviewer_step_num = reviewer_step.get("step", 0)
        coder_steps = []

        for step_result in result.get("steps", []):
            step_info = {
                "step": step_result.get("step"),
                "agent": step_result.get("agent"),
                "description": step_result.get("description"),
                "target_file": step_result.get("output", {}).get("file_path") if step_result.get("output") else None,
                "output": step_result.get("output", {})
            }
            # 只获取在这个 Reviewer 之前的 Coder 步骤
            if (step_info["agent"] == "coder" and
                step_info["step"] < reviewer_step_num):
                coder_steps.append(step_info)

        return coder_steps

    # ==================== P0-2: 只重新生成失败的文件 ====================

    def _regenerate_single_file(
        self,
        task_id: str,
        trace_id: str,
        failed_coder_step: Dict[str, Any],
        current_issues: List[Dict[str, Any]]
    ) -> None:
        """
        只重新生成单个失败的文件，不影响其他文件

        Args:
            task_id: 任务 ID
            trace_id: 追踪 ID
            failed_coder_step: 失败的 Coder 步骤
            current_issues: 当前发现的问题（只针对这个文件）
        """
        target_file = failed_coder_step.get("target_file")
        if not target_file:
            return

        print(f"\n[Regenerate] 只重新生成: {target_file}")

        # 只获取与这个文件相关的问题（使用路径匹配函数）
        issues_for_file = [
            i for i in current_issues
            if _file_paths_match(i.get("file", ""), target_file, self.base_dir)
            or not i.get("file")
        ]

        if not issues_for_file:
            print(f"  [Regenerate] 无相关问题，跳过")
            return

        # 统计问题
        error_count = sum(1 for i in issues_for_file if i.get('severity') == 'error')
        warning_count = sum(1 for i in issues_for_file if i.get('severity') == 'warning')
        info_count = sum(1 for i in issues_for_file if i.get('severity') == 'info')

        # 构建反馈
        feedback_text = f"共发现 {len(issues_for_file)} 个问题 (error: {error_count}, warning: {warning_count}, info: {info_count}):\n\n"
        for severity in ['error', 'warning', 'info']:
            severity_issues = [i for i in issues_for_file if i.get('severity') == severity]
            if severity_issues:
                feedback_text += f"{severity.upper()} 问题 ({len(severity_issues)} 个):\n"
                for issue in severity_issues:
                    feedback_text += f"  - {issue.get('message', '')}\n"
                    suggestion = issue.get('suggestion', '')
                    if suggestion:
                        feedback_text += f"    建议: {suggestion}\n"
                feedback_text += "\n"

        # 读取现有内容
        existing_content = None
        target_path = self.base_dir / target_file
        if target_path.exists():
            try:
                existing_content = target_path.read_text(encoding="utf-8")
                print(f"  [Regenerate] 读取现有文件: {target_file} ({len(existing_content)} 字符)")
            except Exception as e:
                print(f"  [Regenerate] 警告: 无法读取现有文件: {e}")

        # 构建提示
        user_prompt = f"""请修复以下代码文件的所有问题：

文件: {target_file}

"""
        if existing_content:
            user_prompt += f"""现有文件内容（请在此基础上修改）:
```typescript
{existing_content}
```

"""
            user_prompt += f"""⚠️ 关键要求：
1. 保留现有代码中已有的函数、组件、类型定义
2. 只修复问题，不要删除或重复现有代码
3. 确保修复后的代码不引入新的问题
4. 保持现有代码的结构和风格

"""
        user_prompt += f"""{feedback_text}
⚠️ 重要要求：
1. 必须一次性修复所有列出的问题
2. 不要只修复部分问题
3. 确保修复后的代码不引入新的问题

请重新生成修复后的完整代码，确保解决所有问题。"""

        # 获取系统提示词
        system_prompt = self.orchestrator.get_coder_prompt()

        # 调用 Coder
        if self.coder_handler:
            coder_output = self.coder_handler(
                user_prompt=user_prompt,
                system_prompt=system_prompt
            )

            # 提取 JSON
            coder_data = extract_json_from_text(coder_output)
            if not coder_data:
                print(f"  [Regenerate] ✗ 无法解析输出")
                return

            code = coder_data.get("code", "")
            if not code:
                print(f"  [Regenerate] ✗ 未生成代码")
                return

            # 写入文件
            try:
                write_file(target_path, code)
                print(f"  [Regenerate] ✓ 文件已更新: {target_file}")
                self.orchestrator.record_coder_result(trace_id, target_file, code, True)
            except Exception as e:
                print(f"  [Regenerate] ✗ 写入失败: {e}")

    def _regenerate_code_with_feedback(
        self,
        task_id: str,
        trace_id: str,
        coder_steps: List[Dict[str, Any]],
        all_issues: List[Dict[str, Any]]  # 改为接收所有历史问题
    ) -> None:
        """
        根据 Reviewer 的反馈重新生成代码

        Args:
            task_id: 任务 ID
            trace_id: 追踪 ID
            coder_steps: Coder 步骤列表
            all_issues: 所有历史问题（用于全面修复）
        """
        import re

        for coder_step in coder_steps:
            target_file = coder_step.get("target_file")
            if not target_file:
                continue

            print(f"\n[Regenerate] 重新生成: {target_file}")

            # 获取与当前文件相关的问题
            issues_for_file = [i for i in all_issues if i.get("file") == target_file or not i.get("file")]

            if not issues_for_file:
                # 如果当前文件没有问题，跳过
                print(f"  [Regenerate] 文件 {target_file} 无相关问题，跳过")
                continue

            # 统计问题严重程度
            error_count = sum(1 for i in issues_for_file if i.get('severity') == 'error')
            warning_count = sum(1 for i in issues_for_file if i.get('severity') == 'warning')
            info_count = sum(1 for i in issues_for_file if i.get('severity') == 'info')

            # 构建反馈内容（包含所有问题）
            feedback_text = f"共发现 {len(issues_for_file)} 个问题 (error: {error_count}, warning: {warning_count}, info: {info_count}):\n\n"

            # 分组显示问题
            for severity in ['error', 'warning', 'info']:
                severity_issues = [i for i in issues_for_file if i.get('severity') == severity]
                if severity_issues:
                    feedback_text += f"{severity.upper()} 问题 ({len(severity_issues)} 个):\n"
                    for issue in severity_issues:
                        feedback_text += f"  - {issue.get('message', '')}\n"
                        suggestion = issue.get('suggestion', '')
                        if suggestion:
                            feedback_text += f"    建议: {suggestion}\n"
                    feedback_text += "\n"

            # 【关键修复】读取目标文件的现有内容
            existing_file_content = None
            target_path = self.base_dir / target_file
            if target_path.exists():
                try:
                    existing_file_content = target_path.read_text(encoding="utf-8")
                    print(f"  [Regenerate] 读取现有文件: {target_file} ({len(existing_file_content)} 字符)")
                except Exception as e:
                    print(f"  [Regenerate] 警告: 无法读取现有文件: {e}")

            # 构建用户提示
            user_prompt = f"""请修复以下代码文件的所有问题：

文件: {target_file}

"""
            # 添加现有文件内容
            if existing_file_content:
                user_prompt += f"""现有文件内容（请在此基础上修改）:
```typescript
{existing_file_content}
```

"""
                user_prompt += f"""⚠️ 关键要求：
1. 保留现有代码中已有的函数、组件、类型定义
2. 只修复问题，不要删除或重复现有代码
3. 确保修复后的代码不引入新的问题
4. 保持现有代码的结构和风格

"""
            else:
                # 如果没有现有内容，构建代码上下文摘要
                context_summary = self._build_context_summary(coder_steps, issues_for_file)
                user_prompt += f"现有代码上下文:\n{context_summary}\n\n"

            user_prompt += f"""{feedback_text}
⚠️ 重要要求：
1. 必须一次性修复所有列出的问题
2. 不要只修复部分问题，然后等待下一次审查发现其他问题
3. 确保修复后的代码不引入新的问题

请重新生成修复后的完整代码，确保解决所有问题。"""

            # 获取系统提示词
            system_prompt = self.orchestrator.get_coder_prompt()

            # 调用 Coder 处理
            if self.coder_handler:
                coder_output = self.coder_handler(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt
                )

                # 提取 JSON
                coder_data = extract_json_from_text(coder_output)
                if not coder_data:
                    print(f"  [Regenerate] ✗ 无法解析输出，跳过")
                    continue

                code = coder_data.get("code", "")
                if not code:
                    print(f"  [Regenerate] ✗ 未生成代码，跳过")
                    continue

                # 写入文件
                try:
                    full_path = self.base_dir / target_file
                    write_file(full_path, code)
                    print(f"  [Regenerate] ✓ 文件已更新: {target_file}")

                    # 记录结果
                    self.orchestrator.record_coder_result(trace_id, target_file, code, True)
                except Exception as e:
                    print(f"  [Regenerate] ✗ 写入失败: {e}")

    def _execute_step(
        self,
        task_id: str,
        trace_id: str,
        step: Dict[str, Any],
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行单个步骤"""
        step_result = {
            "step": step.get("step"),
            "agent": step.get("agent"),
            "description": step.get("description"),
            "success": False,
            "retries": 0,
            "output": None
        }

        agent = step.get("agent")

        if agent == "coder":
            step_result = self._execute_coder_step(
                task_id, trace_id, step, step_result
            )
        elif agent == "reviewer":
            # Reviewer 步骤应该通过 _execute_step_with_retry 调用
            # 如果直接到这里，说明没有 Coder 步骤需要审查
            step_result = self._execute_reviewer_step(
                task_id, trace_id, step, step_result
            )
        else:
            # 不支持的 Agent 类型
            print(f"\n[Executor] ✗ 不支持的 Agent 类型: {agent}")
            print(f"[Executor] 当前支持的 Agent 类型: coder, reviewer")
            print(f"[Executor] Planner 提示词配置不正确，生成了 {agent} 类型的步骤")
            print(f"[Executor] 请检查 harness/prompts/planner_system.txt 文件")
            print(f"[Executor] 或者修复 Planner 的生成逻辑")
            raise RuntimeError(f"不支持的 Agent 类型: {agent}。当前仅支持: coder, reviewer")

        return step_result

    def _execute_coder_step(
        self,
        task_id: str,
        trace_id: str,
        step: Dict[str, Any],
        step_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行 Coder Agent 步骤"""
        target_file = step.get("target_file", "")
        description = step.get("description", "")
        requirements = step.get("requirements", [])

        print(f"\n[Coder] {description}")
        print(f"  目标文件: {target_file}")

        # 获取 Coder 提示词
        system_prompt = self.orchestrator.get_coder_prompt()

        # 构建用户提示
        user_prompt = self._build_coder_prompt(target_file, description, requirements)

        # 调用 Ducc 处理
        if self.coder_handler:
            coder_output = self.coder_handler(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                code_context=self.code_context
            )
        else:
            print("  [警告] 未注入 coder_handler，使用模拟输出")
            coder_output = json.dumps({
                "file_path": target_file,
                "code": "// 示例代码\nexport function Example() {\n  return <div>Example</div>;\n}",
                "imports": ["react"],
                "explanation": "示例组件"
            })

        # 提取 JSON
        coder_data = extract_json_from_text(coder_output)
        if not coder_data:
            # 打印原始响应以便调试
            print(f"  [Coder] ✗ JSON 解析失败")
            print(f"  [Coder] 原始响应长度: {len(coder_output)} 字符")
            if len(coder_output) > 0:
                print(f"  [Coder] 原始响应前 500 字符:")
                print(f"  {coder_output[:500]}")
            raise RuntimeError(f"无法解析 Coder 输出。原始响应长度: {len(coder_output)}")

        code = coder_data.get("code", "")
        file_path = coder_data.get("file_path", target_file)

        # === NEW: P0-1 Schema 强制校验 ===
        schema_errors = self._validate_coder_output_against_schema(code, file_path)
        if schema_errors:
            print(f"  [Coder] ⚠️ 检测到 {len(schema_errors)} 个 Schema 违规:")
            for err in schema_errors:
                print(f"    - [{err.get('severity')}] {err.get('message', '')}")
            # 不阻止写入，但记录到历史问题
            self.historical_issues.extend(schema_errors)

        # 验证代码不为空
        if not code or len(code) < 10:
            raise RuntimeError(f"生成的代码为空或过短（{len(code)} 字符）")

        # 写入文件
        try:
            full_path = self.base_dir / file_path
            write_file(full_path, code)
            print(f"  [Coder] 文件已写入: {file_path}")

            # === FIX 3: 处理 npm_install（自动安装新依赖）===
            npm_packages = coder_data.get("npm_install", [])
            if npm_packages:
                self._install_npm_packages(npm_packages)

            # 记录结果
            self.orchestrator.record_coder_result(trace_id, file_path, code, True)

            step_result["success"] = True
            step_result["output"] = {"file_path": file_path, "code_length": len(code)}

        except Exception as e:
            print(f"  [Coder] 写入文件失败: {e}")
            self.orchestrator.record_coder_result(trace_id, file_path, "", False)
            raise

        return step_result

    def _install_npm_packages(self, packages: List[str]) -> None:
        """自动安装 npm 包（通过 package.json 管理）

        Args:
            packages: 需要安装的包名列表
        """
        import subprocess
        import os
        if not packages:
            return

        print(f"  [Coder] ⚠️ 检测到需要安装 npm 包: {packages}")

        try:
            # === FIX: 先检查 node_modules 是否已安装 ===
            node_modules = self.base_dir / "node_modules"
            installed = []
            not_installed = []
            for pkg in packages:
                # 检查 @scope/pkg 或 pkg
                pkg_path = node_modules / pkg
                scope_match = None
                if pkg.startswith("@"):
                    parts = pkg.split("/")
                    if len(parts) >= 2:
                        scope_match = node_modules / parts[0] / parts[1]
                
                if pkg_path.exists() or (scope_match and scope_match.exists()):
                    installed.append(pkg)
                else:
                    not_installed.append(pkg)

            if installed:
                print(f"  [Coder] ✓ 已安装: {installed}")
            
            if not not_installed:
                print(f"  [Coder] 所有依赖已安装，无需额外安装")
                return

            print(f"  [Coder] 正在安装依赖: {not_installed}")

            # 读取现有的 package.json
            pkg_path = self.base_dir / "package.json"
            if not pkg_path.exists():
                print(f"  [Coder] 警告: package.json 不存在，跳过依赖安装")
                return

            with open(pkg_path, "r", encoding="utf-8") as f:
                import json as json_module
                pkg_data = json_module.load(f)

            # 追加到 dependencies
            dependencies = pkg_data.get("dependencies", {})
            for pkg in not_installed:
                if pkg not in dependencies:
                    dependencies[pkg] = "latest"
            pkg_data["dependencies"] = dependencies

            # 写回 package.json
            with open(pkg_path, "w", encoding="utf-8") as f:
                json_module.dump(pkg_data, f, indent=2, ensure_ascii=False)

            # === FIX: 使用 npm ci 或增加超时时间 ===
            # 先尝试 npm install（更快）
            result = subprocess.run(
                ["npm", "install"] + not_installed,
                cwd=self.base_dir,
                capture_output=True,
                text=True,
                timeout=300  # 增加超时到 5 分钟
            )

            if result.returncode == 0:
                print(f"  [Coder] ✓ 依赖安装成功: {not_installed}")
            else:
                # 如果失败，尝试带 --legacy-peer-deps
                print(f"  [Coder] 重试安装...")
                result = subprocess.run(
                    ["npm", "install", "--legacy-peer-deps"] + not_installed,
                    cwd=self.base_dir,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode == 0:
                    print(f"  [Coder] ✓ 依赖安装成功: {not_installed}")
                else:
                    print(f"  [Coder] ✗ 依赖安装失败: {result.stderr[:200] if result.stderr else 'unknown error'}")

        except subprocess.TimeoutExpired:
            print(f"  [Coder] 警告: npm install 超时，跳过依赖安装")
        except Exception as e:
            print(f"  [Coder] 警告: 依赖安装失败: {e}")

    def _execute_reviewer_step(
        self,
        task_id: str,
        trace_id: str,
        step: Dict[str, Any],
        step_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行 Reviewer Agent 步骤"""
        description = step.get("description", "审查代码")

        print(f"\n[Reviewer] {description}")

        # 获取所有生成的代码文件
        code_files = self._get_generated_files(task_id, trace_id)

        if not code_files:
            print("  [Reviewer] 没有代码需要审查")
            step_result["success"] = True
            return step_result

        # 获取 Reviewer 提示词
        system_prompt = self.orchestrator.get_reviewer_prompt()

        # 构建审查内容
        review_content = self._build_review_content(code_files)

        # 调用 Ducc 处理
        if self.reviewer_handler:
            reviewer_output = self.reviewer_handler(
                review_content=review_content,
                system_prompt=system_prompt
            )
        else:
            print("  [警告] 未注入 reviewer_handler，使用模拟输出")
            reviewer_output = json.dumps({
                "passed": True,
                "summary": "代码质量良好",
                "issues": [],
                "score": 95
            })

        # 提取 JSON
        review_data = extract_json_from_text(reviewer_output)
        if not review_data:
            raise RuntimeError("无法解析 Reviewer 输出")

        passed = review_data.get("passed", False)
        issues = review_data.get("issues", [])
        score = review_data.get("score", 0)

        print(f"  [Reviewer] 审查结果: {'通过' if passed else '不通过'}")
        print(f"  [Reviewer] 评分: {score}")
        if issues:
            print(f"  [Reviewer] 发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"    - [{issue.get('severity', 'info')}] {issue.get('message')}")

        # 记录结果
        self.orchestrator.record_reviewer_result(trace_id, review_data)

        step_result["success"] = passed
        step_result["output"] = review_data

        return step_result

    def _get_generated_files(self, task_id: str, trace_id: str) -> List[Dict[str, Any]]:
        """获取本次任务生成的代码文件"""
        # 从追踪记录中提取
        trace_file = self.base_dir / "trace" / f"trace-{trace_id}.json"
        if not trace_file.exists():
            return []

        with open(trace_file, "r", encoding="utf-8") as f:
            trace_data = json.load(f)

        files = {}
        for step in trace_data.get("agent_sequence", []):
            if step.get("agent") == "coder" and step.get("output"):
                output = step["output"]
                if "file_path" in output:
                    # 只保留每个文件的最后一次记录（去重）
                    file_path = output["file_path"]
                    files[file_path] = output

        return list(files.values())

    def _build_coder_prompt(
        self,
        target_file: str,
        description: str,
        requirements: List[str]
    ) -> str:
        """构建 Coder 用户提示"""
        prompt = f"请创建/修改以下文件：\n\n"
        prompt += f"文件路径: {target_file}\n"
        prompt += f"需求描述: {description}\n"

        if requirements:
            prompt += f"\n具体要求:\n"
            for req in requirements:
                prompt += f"- {req}\n"

        # 添加可用的导入信息
        prompt += f"\n⚠️ 可用的导入（禁止导入不在此列表中的内容）:\n"

        # 可用的 API 函数
        available_apis = self.code_context.get("available_apis", [])
        if available_apis:
            prompt += f"\n可用 API 函数（从 @/api/noteService 导入）:\n"
            prompt += f"  - getNotes, createNote, updateNote, deleteNote\n"

        # 可用的类型 - 明确指出导入路径
        available_types = self.code_context.get("available_types", [])
        type_files = {}
        for type_def in self.code_context.get("types", []):
            name = type_def["name"]
            file_path = type_def["file_path"]
            # 转换文件路径为导入路径
            if "src/types/" in file_path:
                # 从 @/types/index.ts 导入的
                type_files[name] = "@/types"
            elif "src/api/" in file_path:
                type_files[name] = file_path.replace("src/", "@/").replace(".ts", "")

        if available_types:
            prompt += f"\n可用类型（从 @/types 导入）:\n"
            for type_name in available_types:
                prompt += f"  - {type_name}\n"

        # === FIX: 注入项目的类型定义（权威来源）===
        types_content = self._get_types_content()
        if types_content:
            prompt += f"\n🔴 权威类型定义（src/types/index.ts 的完整内容，必须严格遵守）:\n"
            prompt += f"```typescript\n{types_content}\n```\n"
            prompt += f"⚠️ 所有字段名、类型必须与上述定义完全一致，禁止使用不存在的字段！\n"

        # === FIX: 注入项目中已存在的文件列表 ===
        existing_files = self._get_existing_src_files()
        if existing_files:
            prompt += f"\n📁 项目中已存在的源文件（只能导入以下文件，禁止导入不在此列表中的文件）:\n"
            for f in existing_files:
                prompt += f"  - {f}\n"
            prompt += f"\n⚠️ 导入规则：\n"
            prompt += f"  1. 导入路径中的文件必须在上述列表中\n"
            prompt += f"  2. 禁止导入不存在的文件（如 @/pages/Tags）\n"
            prompt += f"  3. 导入本项目的文件使用命名导入：import {{ XXX }} from '...'\n"

        # === NEW: 添加组件 Schema 契约 ===
        component_schema = self._get_component_schema_for_file(target_file)
        if component_schema:
            prompt += f"\n🟢 组件接口契约（必须严格遵守）:{component_schema}\n"

        # 可用的 Hooks
        available_hooks = self.code_context.get("available_hooks", [])
        if available_hooks:
            prompt += f"\n可用 Hooks（从 @/hooks 导入）:\n"
            prompt += f"  - {', '.join(available_hooks)}\n"

        prompt += f"\n禁止导入:\n"
        prompt += f"  - @/api/mockApi 的任何内容（内部实现）\n"
        prompt += f"  - @/services/* （不存在）\n"
        prompt += f"  - @/hooks/useAuth （不存在）\n"
        prompt += f"  - @/types/noteTypes （不存在，使用 @/types）\n"
        prompt += f"  - @/types/Note.ts （不存在，使用 @/types）\n"

        return prompt

    def _build_review_content(self, code_files: List[Dict[str, Any]]) -> str:
        """构建审查内容"""
        content = """请全面审查以下代码，一次性报告所有发现的问题。

⚠️ 重要要求：
1. 你必须一次性检查代码的所有可能问题，不能分批报告！
2. 不要只报告部分问题，不要期望通过多次迭代逐步发现
3. 第一次审查就是完整审查，必须报告所有问题

以下是待审查的代码：

"""

        for file_data in code_files:
            file_path = file_data.get("file_path", "")
            if not file_path:
                continue

            full_path = self.base_dir / file_path

            content += f"### {file_path}\n\n"

            # 读取实际的代码内容
            if full_path.exists():
                try:
                    code = full_path.read_text(encoding="utf-8")
                    content += "```typescript\n"
                    content += code
                    content += "\n```\n\n"
                except Exception as e:
                    content += f"```typescript\n// 无法读取文件: {e}\n```\n\n"
            else:
                content += "```typescript\n// 文件不存在\n```\n\n"

        content += """
请按照上述要求，仔细检查所有代码，然后输出 JSON 格式的审查结果。
"""
        return content

    def set_handlers(
        self,
        planner: Optional[Callable] = None,
        coder: Optional[Callable] = None,
        reviewer: Optional[Callable] = None
    ) -> None:
        """设置 LLM 处理函数（阿里百炼 API）"""
        if planner:
            self.planner_handler = planner
        if coder:
            self.coder_handler = coder
        if reviewer:
            self.reviewer_handler = reviewer

    def _build_context_summary(self, coder_steps: List[Dict[str, Any]], issues: List[Dict[str, Any]]) -> str:
        """
        构建代码上下文摘要

        Args:
            coder_steps: Coder 步骤列表
            issues: 问题列表

        Returns:
            上下文摘要字符串
        """
        context_parts = []

        # 1. 现有组件列表
        if self.code_context:
            components = self.code_context.get("components", [])
            if components:
                comp_names = [c["name"] for c in components]
                context_parts.append(f"现有组件: {', '.join(comp_names[:20])}")

        # 2. 现有 Hooks 列表
        if self.code_context:
            hooks = self.code_context.get("hooks", [])
            if hooks:
                hook_names = [h["name"] for h in hooks]
                context_parts.append(f"现有 Hooks: {', '.join(hook_names[:20])}")

        # 3. 现有 APIs 列表
        if self.code_context:
            apis = self.code_context.get("apis", [])
            if apis:
                api_names = [a["name"] for a in apis]
                context_parts.append(f"现有 APIs: {', '.join(api_names[:20])}")

        # 4. 相关文件依赖
        related_files = set()
        for issue in issues:
            file = issue.get("file")
            if file:
                related_files.add(file)

        if related_files:
            context_parts.append(f"相关文件: {', '.join(related_files)}")

        # 5. 依赖关系（简化）
        if self.code_context:
            deps = self.code_context.get("dependencies", {})
            if deps:
                # 只显示前5个有依赖的元素
                dep_examples = list(deps.items())[:5]
                dep_text = ", ".join([f"{k}->{', '.join(v)}" for k, v in dep_examples if v])
                context_parts.append(f"依赖关系: {dep_text}")

        return "\n".join(context_parts) if context_parts else "无特定上下文信息"

    def _get_note_type_definition(self) -> str:
        """获取 Note 类型的权威定义（从 types/index.ts 读取）"""
        try:
            types_file = self.base_dir / "src" / "types" / "index.ts"
            if types_file.exists():
                return types_file.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""

    def _get_types_content(self) -> str:
        """获取 src/types/index.ts 的完整内容（通用版本）"""
        return self._get_note_type_definition()

    def _get_existing_src_files(self) -> List[str]:
        """获取 src/ 下所有已存在的 .ts/.tsx 文件列表"""
        src_dir = self.base_dir / "src"
        if not src_dir.exists():
            return []

        files = []
        for f in sorted(src_dir.rglob("*.ts")) + sorted(src_dir.rglob("*.tsx")):
            rel = str(f.relative_to(self.base_dir))
            # 过滤掉 contracts.txt 等非代码文件
            if rel.endswith(('.ts', '.tsx')):
                files.append(rel)
        return files

    def _load_components_schema(self) -> dict:
        """加载组件 Schema 定义"""
        try:
            schema_file = self.base_dir / "harness" / "components_schema.json"
            if schema_file.exists():
                return json.loads(schema_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _get_component_schema_for_file(self, target_file: str) -> str:
        """获取目标文件对应的组件 Schema"""
        schema = self._load_components_schema()
        if not schema:
            return ""

        # 根据文件路径匹配组件
        file_path_lower = target_file.lower()
        for component_name, component_schema in schema.items():
            if component_name.startswith("_"):
                continue
            if isinstance(component_schema, dict) and "file" in component_schema:
                if component_schema["file"].lower() == file_path_lower:
                    return self._format_schema_for_prompt(component_name, component_schema)

        return ""

    def _format_schema_for_prompt(self, component_name: str, schema: dict) -> str:
        """格式化 Schema 为提示文本"""
        lines = []
        lines.append(f"\n📋 {component_name} 组件接口契约:\n")

        # Props
        required = schema.get("required_props", [])
        if required:
            lines.append("必需 Props:")
            for prop in required:
                lines.append(f"  - {prop['name']}: {prop['type']} // {prop['description']}")

        optional = schema.get("optional_props", [])
        if optional:
            lines.append("\n可选 Props:")
            for prop in optional:
                default = prop.get("default", "")
                default_str = f" (默认: {default})" if default else ""
                lines.append(f"  - {prop['name']}: {prop['type']}{default_str} // {prop['description']}")

        # antd 组件
        antd = schema.get("antd_components", [])
        if antd:
            lines.append(f"\nantd 组件: {', '.join(antd)}")

        # 图标
        icons = schema.get("@ant-design/icons", [])
        if icons:
            lines.append(f"图标: {', '.join(icons)}")

        # 示例
        example = schema.get("example", {})
        if example:
            if "props_usage" in example:
                lines.append(f"\n使用示例: {example['props_usage']}")

        # 注意事项
        note = schema.get("note", "")
        if note:
            lines.append(f"\n注意: {note}")

        return "\n".join(lines)

    # ==================== P0-1: Schema 强制校验 ====================

    def _validate_coder_output_against_schema(self, code: str, target_file: str) -> List[Dict[str, Any]]:
        """
        验证 Coder 输出是否符合 Schema
        这是阻止错误代码写入的最后防线
        """
        errors = []
        schema = self._load_components_schema()

        # 1. 检查是否使用了 Note 中不存在的字段
        invalid_fields_patterns = [
            (r'\bnote\.title\b', "Note 类型中不存在 title 字段（只有 content）"),
            (r'\bnote\.tags\b', "Note 类型中不存在 tags 字段"),
            (r'\btag\.id\b', "Note 类型中不存在 tag 字段"),
            (r'\btag\.name\b', "Note 类型中不存在 tag 字段"),
            (r'\bnote\.isPinned\b', "Note 类型中字段是 isTop，不是 isPinned"),
            (r'\bnote\.isArchived\b', "Note 类型中字段是 isArchive，不是 isArchived"),
            (r'\bnote\.createdAt\b', "Note 类型中字段是 createTime，不是 createdAt"),
        ]

        for pattern, message in invalid_fields_patterns:
            import re
            if re.search(pattern, code):
                errors.append({
                    "severity": "error",
                    "category": "type-consistency",
                    "message": message,
                    "suggestion": f"请使用 @/types/index.ts 中定义的正确字段名"
                })

        # 2. 根据目标文件匹配组件 Schema
        component_name = self._match_component_name(target_file, schema)
        if component_name and component_name in schema:
            component_schema = schema[component_name]

            # 检查 Props 接口定义
            props_interface = component_schema.get("props_interface", "")
            if props_interface and f"interface {props_interface}" not in code:
                # 检查是否有内联 Props 定义
                if "interface NoteCardProps" not in code and "NoteCardProps" not in code:
                    pass  # 可能是函数式 Props，暂时不报错

            # 3. 检查必需回调是否被调用
            required_props = component_schema.get("required_props", [])
            for prop in required_props:
                prop_name = prop.get("name", "")
                if prop_name.startswith("on") and prop_name not in code:
                    # 可能是简写或内联，检查是否在组件调用中有
                    if f"onToggleArchive" in prop_name and "onToggleArchive" not in code:
                        if "onArchive" in code:
                            errors.append({
                                "severity": "error",
                                "category": "interface-contract",
                                "message": f"缺少必需回调: {prop_name}",
                                "suggestion": f"组件必须提供 {prop_name} 回调"
                            })

        return errors

    def _match_component_name(self, target_file: str, schema: dict) -> str:
        """根据目标文件路径匹配组件名"""
        file_lower = target_file.lower()
        for component_name, component_schema in schema.items():
            if component_name.startswith("_"):
                continue
            if isinstance(component_schema, dict) and "file" in component_schema:
                if component_schema["file"].lower() == file_lower:
                    return component_name
        return ""

    # ==================== P1-1: 文件路径校验 ====================

    def _validate_plan_paths(self, plan: ExecutionPlan) -> List[Dict[str, Any]]:
        """校验 Planner 生成的文件路径"""
        errors = []

        for step in plan.steps:
            if step.get("agent") != "coder":
                continue

            target = step.get("target_file", "")
            desc = step.get("description", "")

            if not target:
                errors.append({
                    "severity": "error",
                    "message": f"[{desc}] 缺少目标文件路径"
                })
                continue

            # 路径必须以 src/ 开头
            if not target.startswith("src/"):
                errors.append({
                    "severity": "error",
                    "message": f"[{desc}] 路径必须以 src/ 开头: {target}"
                })

            # 校验父目录存在
            full_path = self.base_dir / target
            parent = full_path.parent
            if not parent.exists():
                errors.append({
                    "severity": "error",
                    "message": f"[{desc}] 父目录不存在: {parent}"
                })

        return errors

    # ==================== P2: 预生成契约文件 ====================

    def _generate_contracts_file(self) -> None:
        """在所有 Coder 执行前，先写入契约文件（仅用于参考，不参与编译）"""
        schema = self._load_components_schema()
        if not schema:
            return

        # 生成契约参考文件（使用 .txt 扩展名，不参与 TypeScript 编译）
        contracts_txt = """// 自动生成的组件接口契约（参考文件）
// 此文件仅供参考，不参与 TypeScript 编译

"""

        for component_name, component_schema in schema.items():
            if component_name.startswith("_"):
                continue

            if not isinstance(component_schema, dict):
                continue

            props_interface = component_schema.get("props_interface", "")
            description = component_schema.get("description", "")
            required = component_schema.get("required_props", [])
            optional = component_schema.get("optional_props", [])
            file_path = component_schema.get("file", "")

            lines = [
                f"// ========================================",
                f"// {component_name}",
                f"// 描述: {description}",
                f"// 文件: {file_path}",
                f"// ========================================"
            ]

            if required or optional:
                lines.append(f"// Props 接口: {props_interface}")
                for prop in required:
                    lines.append(f"//   {prop['name']}: {prop['type']} // {prop['description']}")
                for prop in optional:
                    lines.append(f"//   {prop['name']}?: {prop['type']} // {prop['description']} (可选)")
            else:
                lines.append(f"// 无 Props 接口（自包含路由逻辑或无状态组件）")

            lines.append("")

            contracts_txt += "\n".join(lines) + "\n"

        # 写入契约参考文件（.txt 不参与编译）
        contracts_path = self.base_dir / "src" / "contracts.txt"
        contracts_path.write_text(contracts_txt, encoding="utf-8")
        print(f"  [AutoExecutor] 已生成契约参考文件: {contracts_path}")

    def _enrich_code_context(self) -> None:
        """
        扩展 code_context 包含详细信息

        添加：
        - API 函数的详细签名
        - 类型定义的字段列表
        - Hook 的参数和返回值
        - 导入导出关系
        - 明确的可用导入列表
        """
        # API 详细信息
        api_details = []
        available_apis = []  # 可用的 API 函数列表
        for api in self.code_context.get("apis", []):
            name = api["name"]
            return_type = api.get("return_type", "void")
            is_async = api.get("is_async", False)
            async_prefix = "async " if is_async else ""
            api_details.append(f"- {async_prefix}{name}(): {return_type} ({api['file_path']})")
            available_apis.append(name)

        if api_details:
            self.code_context["api_details"] = api_details
            self.code_context["available_apis"] = available_apis

        # 类型字段详细信息
        type_details = []
        available_types = []  # 可用的类型列表
        for type_def in self.code_context.get("types", []):
            name = type_def["name"]
            kind = type_def.get("kind", "interface")
            fields = type_def.get("fields", [])
            file_path = type_def["file_path"]

            if fields:
                field_list = ", ".join(fields)
                type_details.append(f"- {name} ({kind}): [{field_list}] ({file_path})")
            else:
                type_details.append(f"- {name} ({kind}): {{}} ({file_path})")

            available_types.append(name)

        if type_details:
            self.code_context["type_details"] = type_details
            self.code_context["available_types"] = available_types

        # Hook 详细信息
        available_hooks = []  # 可用的 Hook 列表
        for hook in self.code_context.get("hooks", []):
            hook_name = hook["name"]
            available_hooks.append(hook_name)

        if available_hooks:
            self.code_context["available_hooks"] = available_hooks

    def _rescan_code(self) -> None:
        """增量代码扫描：重新扫描项目代码，更新 code_context

        在每步 coder 成功后调用，让后续步骤感知已有代码结构，
        避免 code_context 始终为全 0。
        """
        print(f"\n[CodeScanner] 增量扫描现有代码...")
        self.code_context = self.code_scanner.scan_all()
        self._enrich_code_context()
        print(f"  [CodeScanner] 当前: {self.code_context['summary']['total_components']} 组件, "
              f"{self.code_context['summary']['total_hooks']} Hooks, "
              f"{self.code_context['summary']['total_apis']} APIs, "
              f"{self.code_context['summary'].get('total_types', 0)} 类型")
