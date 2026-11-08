"""Pix2Text（MIT License, breezedeus）latex_ocr.py 的 LaTeX 後處理，原樣複製，讓 ort_mfr.py 不用匯入 pix2text。"""
import re
import string


def remove_redundant_script(text):
    # change '^ { abc }' to 'abc'
    pattern = r'^\^\s*{\s*(.*?)\s*}'
    result = re.sub(pattern, r'\1', text)
    # change '_ { abc }' to 'abc'
    pattern = r'^_\s*{\s*(.*?)\s*}'
    result = re.sub(pattern, r'\1', result)
    return result.strip()


def replace_illegal_symbols(text):
    illegal_to_legals = [
        (r'\\\.', r'\\ .'),  # \. -> \ .
        (r'\\=', r'\\ ='),  # \= -> \ =
        (r'\\-', r'\\ -'),  # \- -> \ -
        (r'\\~', r'\\ ~'),  # \~ -> \ ~
    ]
    for illegal, legal in illegal_to_legals:
        text = re.sub(illegal, legal, text)
    return text


def remove_empty_text(latex_expression):
    # change 'abc ^' to 'abc'
    patterns = [
        r'\\hat\s*{\s*}',  # 匹配 \hat{}
        r'\^\s*{\s*}',  # 匹配 ^{}
        r'_\s*{\s*}',  # 匹配 _{}
        r'\\text\s*{\s*}',  # 匹配 \text{}
        r'\\tilde\s*{\s*}',  # 匹配 \tilde{}
        r'\\bar\s*{\s*}',  # 匹配 \bar{}
        r'\\vec\s*{\s*}',  # 匹配 \vec{}
        r'\\acute\s*{\s*}',  # 匹配 \acute{}
        r'\\grave\s*{\s*}',  # 匹配 \grave{}
        r'\\breve\s*{\s*}',  # 匹配 \breve{}
        r'\\overline\s*{\s*}',  # 匹配 \overline{}
        r'\\dot\s*{\s*}',  # 匹配 \dot{}
        r'\\ddot\s*{\s*}',  # 匹配 \ddot{}
        r'\\widehat\s*{\s*}',  # 匹配 \widehat{}
        r'\\widetilde\s*{\s*}',  # 匹配 \widetilde{}
    ]

    # 使用 sub 函数进行替换
    for pattern in patterns:
        latex_expression = re.sub(pattern, '', latex_expression)
    return latex_expression.strip()


latex_whitespace_symbols = [
    r'\\ +',  # 空格
    r'\\quad\s*',  # 1em 宽度的空白
    r'\\qquad\s*',  # 2em 宽度的空白
    r'\\,\s*',  # 窄空格
    r'\\:\s*',  # 中等空格
    r'\\;\s*',  # 大空格
    r'\\enspace\s*',  # 1em 空白
    r'\\thinspace\s*',  # 1/6em 空白
    r'\\!\s*',  # 感叹号
]


def remove_trailing_whitespace(latex_str):
    # 定义匹配末尾空白符号的正则表达式模式
    trailing_whitespace_pattern = r'(?:' + '|'.join(latex_whitespace_symbols) + r')+$'

    # 使用 sub 函数进行替换
    return re.sub(trailing_whitespace_pattern, '', latex_str).strip()


def remove_unnecessary_spaces(latex_str):
    # Preserve space between a command and a following uppercase letter
    latex_str = re.sub(r'\\([a-zA-Z]+) (?=[a-zA-Z])', r'\\\1 ', latex_str)

    # Remove spaces after commands not followed by an uppercase letter, carefully not affecting commands that require space
    latex_str = re.sub(r'\\([a-zA-Z]+)\s+(?![a-zA-Z])', r'\\\1', latex_str)

    # Remove spaces around curly braces, preserving internal spaces
    latex_str = re.sub(r'(\{)\s+', r'\1', latex_str)
    latex_str = re.sub(r'\s+(\})', r'\1', latex_str)

    # Specifically target and remove spaces around mathematical operators, including +, -, =, and similar operators
    latex_str = re.sub(r'(?<=[^\\])\s*([+\-=])\s*', r'\1', latex_str)

    # Remove spaces around "^" and "_" for subscripts and superscripts
    latex_str = re.sub(r'\s*(\^|\_)\s*', r'\1', latex_str)

    return latex_str


def find_all_left_or_right(latex, left_or_right='left'):
    left_bracket_infos = []
    prefix_len = len(left_or_right) + 1
    # 匹配出latex中所有的 '\left' 后面跟着的第一个非空格字符，定位它们所在的位置
    for m in re.finditer(rf'\\{left_or_right}\s*\S', latex):
        start, end = m.span()
        if latex[end - 1] in string.ascii_letters:
            continue
        # 如果最后一个字符为 "\"，则往前继续匹配，直到匹配到一个非字母的字符
        # 如 "\left \big("
        while latex[end - 1] in ('\\', ' '):
            end += 1
            while end < len(latex) and latex[end].isalpha():
                end += 1
        ori_str = latex[start + prefix_len : end].strip()
        # FIXME: ori_str中可能出现多个 '\left'，此时需要分隔开

        left_bracket_infos.append({'str': ori_str, 'start': start, 'end': end})
        left_bracket_infos.sort(key=lambda x: x['start'])
    return left_bracket_infos


def match_left_right(left_str, right_str):
    """匹配左右括号，如匹配 `\left(` 和 `\right)`。"""
    left_str = left_str.strip().replace(' ', '')[len('left') + 1 :]
    right_str = right_str.strip().replace(' ', '')[len('right') + 1 :]
    # 去掉开头的相同部分
    while left_str and right_str and left_str[0] == right_str[0]:
        left_str = left_str[1:]
        right_str = right_str[1:]

    match_pairs = [
        ('', ''),
        ('(', ')'),
        ('\{', '.'),  # 大括号那种
        ('⟮', '⟯'),
        ('[', ']'),
        ('⟨', '⟩'),
        ('{', '}'),
        ('⌈', '⌉'),
        ('┌', '┐'),
        ('⌊', '⌋'),
        ('└', '┘'),
        ('⎰', '⎱'),
        ('lt', 'gt'),
        ('lang', 'rang'),
        (r'langle', r'rangle'),
        (r'lbrace', r'rbrace'),
        ('lBrace', 'rBrace'),
        (r'lbracket', r'rbracket'),
        (r'lceil', r'rceil'),
        ('lcorner', 'rcorner'),
        (r'lfloor', r'rfloor'),
        (r'lgroup', r'rgroup'),
        (r'lmoustache', r'rmoustache'),
        (r'lparen', r'rparen'),
        (r'lvert', r'rvert'),
        (r'lVert', r'rVert'),
    ]
    return (left_str, right_str) in match_pairs


def fix_latex(latex: str) -> str:
    """对识别结果做进一步处理和修正。"""
    # # 把latex中的中文括号全部替换成英文括号
    # latex = latex.replace('（', '(').replace('）', ')')
    # # 把latex中的中文逗号全部替换成英文逗号
    # latex = latex.replace('，', ',')

    left_bracket_infos = find_all_left_or_right(latex, left_or_right='left')
    right_bracket_infos = find_all_left_or_right(latex, left_or_right='right')
    # left 和 right 找配对，left找位置比它靠前且最靠近他的right配对
    for left_bracket_info in left_bracket_infos:
        for right_bracket_info in right_bracket_infos:
            if (
                not right_bracket_info.get('matched', False)
                and right_bracket_info['start'] > left_bracket_info['start']
                and match_left_right(
                    right_bracket_info['str'], left_bracket_info['str']
                )
            ):
                left_bracket_info['matched'] = True
                right_bracket_info['matched'] = True
                break

    for left_bracket_info in left_bracket_infos:
        # 把没有匹配的 '\left'替换为等长度的空格
        left_len = len('left') + 1
        if not left_bracket_info.get('matched', False):
            start_idx = left_bracket_info['start']
            end_idx = start_idx + left_len
            latex = (
                latex[: left_bracket_info['start']]
                + ' ' * (end_idx - start_idx)
                + latex[end_idx:]
            )
    for right_bracket_info in right_bracket_infos:
        # 把没有匹配的 '\right'替换为等长度的空格
        right_len = len('right') + 1
        if not right_bracket_info.get('matched', False):
            start_idx = right_bracket_info['start']
            end_idx = start_idx + right_len
            latex = (
                latex[: right_bracket_info['start']]
                + ' ' * (end_idx - start_idx)
                + latex[end_idx:]
            )

    # 把 latex 中的连续空格替换为一个空格
    latex = re.sub(r'\s+', ' ', latex)
    return latex.strip()


def post_process(text):
    text = remove_redundant_script(text)
    text = remove_trailing_whitespace(text)
    text = replace_illegal_symbols(text)
    for _ in range(10):
        new_text = remove_empty_text(text)
        if new_text == text:
            break
        text = new_text

    text = fix_latex(text)
    text = remove_unnecessary_spaces(text)
    return text.strip()

