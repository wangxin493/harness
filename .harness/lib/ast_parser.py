#!/usr/bin/env python3
"""AST 解析器 —— tree-sitter 封装

职责：纯语法解析。把 TS/TSX 源码解析为中性的 ParseResult。
不做：路径规则、组件/Hook/API 分类（这些在 scanner 中根据 rules.yaml 完成）。

P0 提取的语法节点：
- 静态 import / export-from（依赖图边）
- export function / const / class / interface / type / enum / default
- 函数返回是否包含 JSX（用于后续区分组件 vs 普通函数）
- import type / export type 标记（默认 type-only 不进依赖图）

P0 不做：动态 import()、require()、装饰器、namespace。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# 数据结构（中性，不含路径分类语义）
# ---------------------------------------------------------------------------


@dataclass
class ImportRef:
    """单条 import / export-from 边。"""

    source: str           # 原始字符串："@/hooks/useFoo"、"react"、"./util"
    is_type_only: bool    # `import type ...` 或 `export type ... from ...`
    is_reexport: bool     # 由 `export ... from "..."` 产生
    line: int             # 1-indexed


@dataclass
class ExportItem:
    """单个导出项。"""

    name: str             # 导出名；default 导出记为 "default"
    kind: str             # function | class | const | let | var | interface | type | enum | default | reexport
    line: int             # 1-indexed
    returns_jsx: bool = False   # 仅 function / arrow / const 有意义
    is_type_only: bool = False  # interface / type alias / `export type`


@dataclass
class HookCall:
    """useXxx() 形式的调用点。

    用于 React Rules-of-Hooks 项目层叠加版校验：
    - in_function: 包含此调用的最近 named function/arrow 名字（无名时填 ""）
    - in_function_kind: function | arrow | method | top_level
    - in_branch: 是否处于 if/for/while/&&/||/?: 等条件路径上（粗判，宁严勿松）
    """

    callee: str                  # "useState" / "useEffect" / "useMyHook"
    line: int                    # 1-indexed
    in_function: str             # 包裹函数名；顶层调用为 ""
    in_function_kind: str        # function | arrow | method | top_level
    in_branch: bool              # 是否在条件 / 循环里


@dataclass
class ParseResult:
    """解析结果。"""

    file_path: str
    language: str                 # "typescript" | "tsx"
    imports: List[ImportRef] = field(default_factory=list)
    exports: List[ExportItem] = field(default_factory=list)
    hook_calls: List[HookCall] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


class TypeScriptParser:
    """tree-sitter-typescript 0.21 封装。

    - .ts  → typescript grammar
    - .tsx → tsx grammar
    - 其它扩展名 → ValueError
    """

    _ts_language = None
    _tsx_language = None

    def __init__(self) -> None:
        # 延迟到首次 parse 时加载，避免 import 期就触发原生库链接
        self._ts_parser = None
        self._tsx_parser = None

    # -- 公共入口 -----------------------------------------------------------

    def parse(self, file_path: Path, source: Optional[bytes] = None) -> ParseResult:
        """解析单文件。source 可由调用方提供以避免重复读盘。"""

        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix == ".ts":
            language = "typescript"
            parser = self._get_ts_parser()
        elif suffix == ".tsx":
            language = "tsx"
            parser = self._get_tsx_parser()
        else:
            raise ValueError(f"unsupported file extension: {suffix} ({path})")

        if source is None:
            source = path.read_bytes()

        tree = parser.parse(source)
        result = ParseResult(file_path=str(path), language=language)

        self._collect_parse_errors(tree.root_node, result)
        self._walk_top_level(tree.root_node, source, result)
        self._collect_hook_calls(tree.root_node, source, result)

        return result

    # -- tree-sitter 资源延迟加载 -------------------------------------------

    @classmethod
    def _load_languages(cls) -> None:
        if cls._ts_language is not None:
            return
        # 延迟 import：让 ImportError 在第一次真正解析时才暴露，方便 doctor 提示
        from tree_sitter import Language  # type: ignore
        import tree_sitter_typescript as tstypescript  # type: ignore

        # tree-sitter 0.21.x: Language(language_capsule, name)
        cls._ts_language = Language(tstypescript.language_typescript(), "typescript")
        cls._tsx_language = Language(tstypescript.language_tsx(), "tsx")

    def _get_ts_parser(self):
        if self._ts_parser is None:
            self._load_languages()
            from tree_sitter import Parser  # type: ignore

            parser = Parser()
            parser.set_language(self._ts_language)
            self._ts_parser = parser
        return self._ts_parser

    def _get_tsx_parser(self):
        if self._tsx_parser is None:
            self._load_languages()
            from tree_sitter import Parser  # type: ignore

            parser = Parser()
            parser.set_language(self._tsx_language)
            self._tsx_parser = parser
        return self._tsx_parser

    # -- 节点遍历 -----------------------------------------------------------

    def _collect_parse_errors(self, root, result: ParseResult) -> None:
        """记录 tree-sitter 标注的 ERROR/MISSING 节点位置（不阻断解析）。"""
        stack = [root]
        while stack:
            node = stack.pop()
            if node.is_error or node.is_missing:
                line = node.start_point[0] + 1
                result.parse_errors.append(
                    f"{'MISSING' if node.is_missing else 'ERROR'} at line {line}"
                )
            stack.extend(node.children)

    def _walk_top_level(self, root, source: bytes, result: ParseResult) -> None:
        """只遍历顶层 program 节点的直接子节点。"""
        for child in root.children:
            kind = child.type
            if kind == "import_statement":
                self._handle_import(child, source, result)
            elif kind == "export_statement":
                self._handle_export(child, source, result)
            # 其它顶层语句（函数声明、变量声明等）暂不直接收集；
            # 仅 `export ...` 包裹的声明会被记入 exports。

    # -- hook 调用收集 ------------------------------------------------------

    # 是否被视为分支节点（hook 调用出现在这些子树内 → in_branch=True）
    _BRANCH_NODES = frozenset({
        "if_statement",
        "else_clause",
        "switch_statement",
        "for_statement",
        "for_in_statement",
        "for_of_statement",
        "while_statement",
        "do_statement",
        "ternary_expression",
        # `a && useX()` / `a || useX()` —— 条件求值
        # 注意：tree-sitter 把所有二元都叫 binary_expression，是否分支要看 operator
        # 这里在 walker 里手动判 operator 而不是把 binary_expression 全列入
    })

    # 函数子树根（进入这里要新开 function context）
    _FUNCTION_NODES = frozenset({
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
    })

    def _collect_hook_calls(self, root, source: bytes, result: ParseResult) -> None:
        """递归整个 AST 收集 useXxx() 调用，记录所在函数 + 是否在分支里。

        识别 callee：
          - 最简形式：identifier 节点，文本以 ``use`` + 大写字母 / 数字开头
          - 不识别 ``a.useFoo()`` / ``useFoo.bar()``（成员调用），交由更高层规则处理
        """
        # 工作栈：(node, fn_name, fn_kind, in_branch)
        work = [(root, "", "top_level", False)]
        while work:
            node, fn_name, fn_kind, in_branch = work.pop()

            # 当前节点本身是 call_expression：先判 hook，再继续向下
            if node.type == "call_expression":
                callee = node.child_by_field_name("function")
                if callee is not None and callee.type == "identifier":
                    text = self._node_text(callee, source)
                    if self._is_hook_name(text):
                        result.hook_calls.append(HookCall(
                            callee=text,
                            line=node.start_point[0] + 1,
                            in_function=fn_name,
                            in_function_kind=fn_kind,
                            in_branch=in_branch,
                        ))

            # 决定子节点的上下文
            new_fn_name, new_fn_kind = fn_name, fn_kind
            if node.type in self._FUNCTION_NODES:
                new_fn_name = self._infer_function_name(node, source) or ""
                new_fn_kind = self._function_kind_label(node.type)

            # 入子节点
            for child in node.children:
                child_branch = in_branch or self._enters_branch(node, child)
                # 一旦进入新的函数子树，"分支" 重置 —— hook 在新函数内部不再算"上层分支"
                if child.type in self._FUNCTION_NODES:
                    child_branch = False
                work.append((child, new_fn_name, new_fn_kind, child_branch))

    @staticmethod
    def _is_hook_name(text: str) -> bool:
        """判定标识符是否像 React hook 名（use + 大写字母开头）。

        排除 ``use`` 本身、``user``、``useless`` 等：必须是 ``use`` + 大写字母。
        ``useX`` 这种单字母 hook 也允许。
        """
        if not text.startswith("use") or len(text) <= 3:
            return False
        return text[3].isupper()

    def _enters_branch(self, parent, child) -> bool:
        """判定 child 是否属于 parent 的"分支"子树。

        - parent 在 _BRANCH_NODES：除了 condition 本身，进入 consequence/alternative
          都算分支；这里粗略地把所有非 condition 子节点都视为分支（保守）。
        - parent 是 binary_expression 且 operator ∈ {&&, ||, ??}：右操作数算分支；
          左操作数永远求值，不算。
        """
        if parent.type in self._BRANCH_NODES:
            # condition 字段本身（if/while/ternary 的判断式）不进入分支体
            cond = parent.child_by_field_name("condition")
            if cond is not None and child == cond:
                return False
            # do_statement 的 body 也算分支体（do { useX() } while(cond) → 一定执行，
            # 但 RoH 的语义还是要求 hook 不在 do/while 里，因为重复次数取决于运行时）
            return True

        if parent.type == "binary_expression":
            # 在 children 里找 &&/||/?? 这种 operator token
            short_circuit = any(c.type in ("&&", "||", "??") for c in parent.children)
            if short_circuit:
                left = parent.child_by_field_name("left")
                if left is not None and child != left:
                    return True
        return False

    def _infer_function_name(self, fn_node, source: bytes) -> str:
        """推断函数名。

        - function_declaration / class method 直接读 name 字段
        - arrow_function / function_expression：看父链上的 variable_declarator name 或 pair key
        """
        t = fn_node.type
        if t in ("function_declaration", "generator_function_declaration", "method_definition"):
            return self._field_text(fn_node, "name", source) or ""
        # arrow / function expression：往上找
        parent = fn_node.parent
        if parent is None:
            return ""
        if parent.type == "variable_declarator":
            return self._field_text(parent, "name", source) or ""
        if parent.type == "pair":
            key = parent.child_by_field_name("key")
            if key is not None:
                return self._node_text(key, source)
        if parent.type == "assignment_expression":
            left = parent.child_by_field_name("left")
            if left is not None:
                return self._node_text(left, source)
        return ""

    @staticmethod
    def _function_kind_label(node_type: str) -> str:
        if node_type in ("function_declaration", "generator_function_declaration", "function_expression"):
            return "function"
        if node_type == "arrow_function":
            return "arrow"
        if node_type == "method_definition":
            return "method"
        return "function"

    # -- import_statement ---------------------------------------------------

    def _handle_import(self, node, source: bytes, result: ParseResult) -> None:
        """处理 `import [type] ... from "source"`。

        type-only 判定：
        - `import type ... from "x"`  → is_type_only=True
        - `import { type A, B } from "x"` → 混合：保守按 False（仍是运行时依赖）
        """
        source_str = self._extract_import_source(node, source)
        if source_str is None:
            return

        is_type_only = self._is_import_type_only(node, source)
        line = node.start_point[0] + 1

        result.imports.append(
            ImportRef(
                source=source_str,
                is_type_only=is_type_only,
                is_reexport=False,
                line=line,
            )
        )

    def _extract_import_source(self, node, source: bytes) -> Optional[str]:
        """从 import_statement 中提取 from "x" 的字符串字面量内容。"""
        # tree-sitter-typescript 中 import_statement 末尾有一个 string 节点
        for child in node.children:
            if child.type == "string":
                return self._string_literal_value(child, source)
        return None

    def _is_import_type_only(self, node, source: bytes) -> bool:
        """识别 `import type ...` 形式。

        语法树里 `import type X from "y"` 在 import 关键字后会有一个
        匿名 token "type"。按子节点的源码文本判定，避免依赖具体语法节点名。
        """
        for child in node.children:
            if child.type == "import":
                continue
            if child.type == "string":
                break
            text = source[child.start_byte:child.end_byte]
            if text == b"type":
                return True
            # 遇到 import_clause 之后就停（防止误把 specifier 内的 type 当顶层）
            if child.type == "import_clause":
                break
        return False

    # -- export_statement ---------------------------------------------------

    def _handle_export(self, node, source: bytes, result: ParseResult) -> None:
        """处理多种 export 形态。

        覆盖：
        - export { a, b } from "x"     → ImportRef(is_reexport=True)
        - export * from "x"            → 同上
        - export type { a } from "x"   → 同上 + is_type_only=True
        - export function foo() {}     → ExportItem(kind=function)
        - export const x = ...         → ExportItem(kind=const)
        - export class / interface / type / enum / default
        """
        # 1) re-export：节点中包含 string 字面量
        string_child = next((c for c in node.children if c.type == "string"), None)
        if string_child is not None:
            self._handle_reexport(node, string_child, source, result)
            return

        # 2) 声明型 export：找出被 export 的声明子节点
        is_default = any(
            source[c.start_byte:c.end_byte] == b"default"
            for c in node.children
            if c.type == "default" or (not c.is_named and c.type not in ("export",))
        )

        decl_node = None
        for child in node.children:
            t = child.type
            if t in (
                "function_declaration",
                "generator_function_declaration",
                "class_declaration",
                "abstract_class_declaration",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
                "lexical_declaration",   # const / let
                "variable_declaration",  # var
            ):
                decl_node = child
                break

        if decl_node is None:
            # export { a, b }（无 from）—— 局部 re-bind，P0 不收
            if is_default:
                line = node.start_point[0] + 1
                result.exports.append(
                    ExportItem(name="default", kind="default", line=line)
                )
            return

        self._collect_declaration_export(decl_node, source, result, is_default)

    def _handle_reexport(self, node, string_child, source: bytes, result: ParseResult) -> None:
        """export ... from "x" 形态。"""
        source_str = self._string_literal_value(string_child, source)
        if source_str is None:
            return

        is_type_only = False
        for child in node.children:
            if child.type == "export":
                continue
            if child is string_child:
                break
            text = source[child.start_byte:child.end_byte]
            if text == b"type":
                is_type_only = True
                break

        line = node.start_point[0] + 1
        result.imports.append(
            ImportRef(
                source=source_str,
                is_type_only=is_type_only,
                is_reexport=True,
                line=line,
            )
        )
        # 同时记一条 reexport export 项，方便后续做"导出曲面"分析
        result.exports.append(
            ExportItem(
                name="*",
                kind="reexport",
                line=line,
                is_type_only=is_type_only,
            )
        )

    def _collect_declaration_export(
        self, decl_node, source: bytes, result: ParseResult, is_default: bool
    ) -> None:
        """从被 export 的声明节点提取 ExportItem。"""
        line = decl_node.start_point[0] + 1
        t = decl_node.type

        if t in ("function_declaration", "generator_function_declaration"):
            name = self._field_text(decl_node, "name", source) or ("default" if is_default else "")
            returns_jsx = self._function_returns_jsx(decl_node)
            result.exports.append(
                ExportItem(
                    name=name or "default",
                    kind="default" if is_default else "function",
                    line=line,
                    returns_jsx=returns_jsx,
                )
            )
            return

        if t in ("class_declaration", "abstract_class_declaration"):
            name = self._field_text(decl_node, "name", source) or ("default" if is_default else "")
            result.exports.append(
                ExportItem(
                    name=name or "default",
                    kind="default" if is_default else "class",
                    line=line,
                )
            )
            return

        if t == "interface_declaration":
            name = self._field_text(decl_node, "name", source) or ""
            result.exports.append(
                ExportItem(name=name, kind="interface", line=line, is_type_only=True)
            )
            return

        if t == "type_alias_declaration":
            name = self._field_text(decl_node, "name", source) or ""
            result.exports.append(
                ExportItem(name=name, kind="type", line=line, is_type_only=True)
            )
            return

        if t == "enum_declaration":
            name = self._field_text(decl_node, "name", source) or ""
            result.exports.append(ExportItem(name=name, kind="enum", line=line))
            return

        if t in ("lexical_declaration", "variable_declaration"):
            kind_word = self._first_keyword_text(decl_node, source) or "const"
            for declarator in decl_node.children:
                if declarator.type != "variable_declarator":
                    continue
                name_node = declarator.child_by_field_name("name")
                value_node = declarator.child_by_field_name("value")
                name = self._node_text(name_node, source) if name_node else ""
                returns_jsx = (
                    self._expression_returns_jsx(value_node) if value_node else False
                )
                result.exports.append(
                    ExportItem(
                        name=name,
                        kind=kind_word,
                        line=line,
                        returns_jsx=returns_jsx,
                    )
                )

    # -- JSX 检测 -----------------------------------------------------------

    def _function_returns_jsx(self, fn_node) -> bool:
        """函数声明体内是否出现 jsx_element / jsx_self_closing_element / jsx_fragment。"""
        body = fn_node.child_by_field_name("body")
        if body is None:
            return False
        return self._subtree_contains_jsx(body)

    def _expression_returns_jsx(self, expr_node) -> bool:
        """const x = () => <div/> 或 const x = function(){ return <div/> } 都算。"""
        if expr_node is None:
            return False
        if expr_node.type in ("arrow_function", "function_expression"):
            body = expr_node.child_by_field_name("body")
            if body is None:
                return False
            # 箭头表达式体可能直接是 JSX 节点
            if body.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
                return True
            return self._subtree_contains_jsx(body)
        return False

    def _subtree_contains_jsx(self, node) -> bool:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
                return True
            stack.extend(n.children)
        return False

    # -- 文本提取小工具 ------------------------------------------------------

    @staticmethod
    def _node_text(node, source: bytes) -> str:
        if node is None:
            return ""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _string_literal_value(node, source: bytes) -> Optional[str]:
        """从 string 节点中取出去掉引号的内容。"""
        for child in node.children:
            if child.type == "string_fragment":
                return source[child.start_byte:child.end_byte].decode(
                    "utf-8", errors="replace"
                )
        # 兜底：去掉首尾引号
        raw = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if len(raw) >= 2 and raw[0] in ("'", '"', "`"):
            return raw[1:-1]
        return raw

    def _field_text(self, node, field: str, source: bytes) -> Optional[str]:
        child = node.child_by_field_name(field)
        if child is None:
            return None
        return self._node_text(child, source)

    @staticmethod
    def _first_keyword_text(node, source: bytes) -> Optional[str]:
        """取 lexical_declaration 的 const/let，或 variable_declaration 的 var。"""
        for child in node.children:
            if child.type in ("const", "let", "var"):
                return child.type
        return None
