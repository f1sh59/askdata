"""生成产品介绍书（客户版）Word 文档。

用 python-docx 直接排，正文用宋体、标题用微软雅黑，
主色取项目界面的墨绿 #0F766E，保持和产品视觉一致。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

INK = RGBColor(0x10, 0x14, 0x18)
ACCENT = RGBColor(0x0F, 0x76, 0x6E)
MUTED = RGBColor(0x56, 0x61, 0x6C)
DIM = RGBColor(0x8B, 0x93, 0x9C)

HEI = "微软雅黑"
SONG = "宋体"


# ---------------------------------------------------------------- 基础工具


def set_font(run, name: str, size: float, bold: bool = False, color: RGBColor | None = None):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def para(doc, text="", *, font=SONG, size=10.5, bold=False, color=INK,
         align=None, before=0, after=6, line=1.45, indent=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = line
    if indent:
        p.paragraph_format.first_line_indent = Pt(indent)
    if align is not None:
        p.alignment = align
    if text:
        set_font(p.add_run(text), font, size, bold, color)
    return p


def h1(doc, text, *, before=20, after=8):
    p = para(doc, text, font=HEI, size=15, bold=True, color=ACCENT, before=before, after=2, line=1.25)
    rule = doc.add_paragraph()
    rule.paragraph_format.space_before = Pt(0)
    rule.paragraph_format.space_after = Pt(12)
    # 空段落默认占一整行（10.5pt × 1.45），会把横线推得很远；压到 1pt 贴住标题
    rule.paragraph_format.line_spacing = Pt(1)
    pPr = rule._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "0F766E")
    borders.append(bottom)
    pPr.append(borders)
    return p


def h2(doc, text, *, before=14, after=5):
    return para(doc, text, font=HEI, size=12, bold=True, color=INK, before=before, after=after, line=1.3)


def body(doc, text, *, indent=21, after=6):
    return para(doc, text, indent=indent, after=after)


def bullet(doc, text, *, bold_prefix: str | None = None):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.4
    p.paragraph_format.left_indent = Pt(18)
    if bold_prefix:
        set_font(p.add_run(bold_prefix), HEI, 10.5, True, INK)
    set_font(p.add_run(text), SONG, 10.5, False, INK)
    return p


def quote(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Pt(24)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(10)
    p.paragraph_format.line_spacing = 1.45
    pPr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "10")
    left.set(qn("w:color"), "0F766E")
    borders.append(left)
    pPr.append(borders)
    set_font(p.add_run(text), SONG, 10.5, False, MUTED)
    return p


def shade(cell, hex_color: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def table(doc, rows, widths, *, header=True, font_size=9.5, accent_header=True):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for r_idx, row in enumerate(rows):
        for c_idx, value in enumerate(row):
            cell = t.cell(r_idx, c_idx)
            cell.width = Cm(widths[c_idx])
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(3)
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.line_spacing = 1.25
            is_header = header and r_idx == 0
            set_font(
                p.add_run(str(value)),
                HEI if is_header else SONG,
                font_size,
                bold=is_header,
                color=RGBColor(0xFF, 0xFF, 0xFF) if (is_header and accent_header) else INK,
            )
            if is_header and accent_header:
                shade(cell, "0F766E")
            elif r_idx % 2 == 0:
                shade(cell, "F4F7F6")
    for row in t.rows:
        for c_idx, cell in enumerate(row.cells):
            cell.width = Cm(widths[c_idx])
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


def page_footer(doc, left_text: str, section_index: int = 0):
    footer = doc.sections[section_index].footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(left_text + "  ·  第 "), SONG, 8.5, False, DIM)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run = OxmlElement("w:r")
    fld.append(run)
    p._p.append(fld)
    set_font(p.add_run(" 页"), SONG, 8.5, False, DIM)


def page_break(doc):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def setup(doc, *, landscape=False):
    for section in doc.sections:
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.left_margin = Cm(2.3)
        section.right_margin = Cm(2.3)
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = SONG
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)


# ================================================================
# 产品介绍书（客户版）
# ================================================================


def build_proposal(path: Path) -> None:
    doc = Document()
    setup(doc)

    # ---- 封面 ----
    para(doc, "", after=150)
    para(doc, "askdata", font=HEI, size=13, color=ACCENT, after=6)
    para(doc, "智能问数系统", font=HEI, size=32, bold=True, after=6, line=1.15)
    para(doc, "让业务人员用一句中文，直接问到数据库里的答案", font=HEI, size=13, color=MUTED, after=28)

    rule = doc.add_paragraph()
    rule.paragraph_format.space_after = Pt(24)
    pPr = rule._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:color"), "0F766E")
    borders.append(bottom)
    pPr.append(borders)

    para(doc, "产品介绍书", font=HEI, size=12, color=INK, after=4)
    para(doc, "面向企业数据分析场景 · 可私有化部署", font=SONG, size=10.5, color=MUTED, after=2)
    para(doc, f"{date.today().year} 年 {date.today().month} 月", font=SONG, size=10.5, color=DIM)

    # 封面单独一节：不显示页码，正文从新的一节开始
    doc.add_section(WD_SECTION.NEW_PAGE)
    page_footer(doc, "askdata 智能问数系统 · 产品介绍", section_index=1)

    # ---- 正文 ----
    h1(doc, "一、为什么「让 AI 查数据」这件事一直停在演示阶段", before=0)
    body(doc, "过去两年，几乎每家企业都试过让大模型直接查数据库。演示效果通常很好："
              "一句中文，几秒钟，一张表。但真正要上线时，技术和业务的负责人几乎都会问同样四个问题——")
    table(
        doc,
        [
            ["质疑", "背后的真实风险"],
            ["它算的数字对吗？", "错了没人知道，业务拿着错数据做决策，比没有数据更危险"],
            ["它会不会乱来？", "模型可能生成删表、改数、甚至读取服务器文件的语句"],
            ["它听不懂我的问题怎么办？", "含糊的问题被硬猜成一个答案，看起来很有道理，实际是错的"],
            ["它能接我们的数据吗？", "企业数据格式杂乱，接不进来就永远只是个演示"],
        ],
        [5.6, 10.6],
    )
    body(doc, "askdata 的设计目标不是「能问出数据」，而是让上面四个问题都有明确、可检验的答案。")

    h1(doc, "二、产品定位")
    quote(
        doc,
        "一个可以交付给业务团队使用的智能问数系统：听得懂问题、算得对数字、拦得住风险、接得上数据。",
    )
    body(doc, "它面向的不是数据工程师，而是业务一线——运营、销售、财务、供应链。"
              "他们不需要会写 SQL，只需要会提问题。")

    h1(doc, "三、六项核心能力")

    h2(doc, "1. 听得懂：不确定就反问，而不是猜")
    body(doc, "系统在生成查询之前，先判断这句话的信息够不够。如果缺时间范围、缺口径、缺维度，"
              "它会主动追问，并把数据库里真实可填的选项列出来——比如「数据范围是 2024-01 到 2025-12」"
              "「渠道只有天猫、京东、抖音、官网、小程序这五个」。")
    body(doc, "效果：在 15 道刻意含糊的测试题上，追问率 100%，问到点上的比例 93%，"
              "不该问却问了的比例 0%。追问之后能收敛到正确答案的比例，从 0% 提升到 87%。")

    h2(doc, "2. 算得对：用执行结果判定对错，而不是看 SQL 写得像不像")
    body(doc, "系统内置 50 道中文业务题，每题都有一份标准查询。判定方式是：把标准查询和系统生成的查询"
              "都真正跑一遍，比对结果集——行数、数值、列数全部一致才算对。"
              "这比「看 SQL 像不像」严格得多，也公平得多。")
    table(
        doc,
        [
            ["指标", "结果", "说明"],
            ["执行准确率", "90.0%", "3 次独立运行：88 / 90 / 92，波动 ±2 个百分点"],
            ["不接模型的基线", "18%", "说明这套题确实有难度"],
            ["问题覆盖", "50 题", "单表查询、多表关联、同比环比、窗口函数、占比分摊"],
        ],
        [4.4, 3.6, 8.2],
    )

    h2(doc, "3. 拦得住：四道闸，任何一条不通过就不执行")
    body(doc, "系统不会直接把模型写出的语句交给数据库。它在执行前要过四道关：")
    bullet(doc, "只允许单条查询语句，删表、改数、挂载外部库一律拒绝。", bold_prefix="① 语法白名单：")
    bullet(doc, "禁止读取服务器文件的函数，防止有人通过查询把服务器上的文件读走。", bold_prefix="② 禁止文件函数：")
    bullet(doc, "任何查询都自动加上行数上限，防止一条语句把数据库拖垮。", bold_prefix="③ 强制限流：")
    bullet(doc, "超过 8 秒自动中断，连接随之释放，不会影响其他请求。", bold_prefix="④ 超时熔断：")
    body(doc, "另外还提供可选的**行级权限**：不同角色的用户，查询会被自动改写成只能看到自己有权限的那部分数据。")
    body(doc, "我们用 60 条用例做回归测试：50 条恶意语句全部被拦截，10 条正常业务查询全部放行、零误伤。")

    h2(doc, "4. 接得上：导入自己的数据，马上就能问")
    body(doc, "真实的企业数据往往是一堆 Excel 导出的 CSV：列名带空格和括号、金额带千分位和货币符号、"
              "日期有五种写法、空值是空字符串、整行重复。系统在导入时自动做七项整理：")
    table(
        doc,
        [
            ["整理项", "解决什么"],
            ["去首尾空白", "「  杭州 」→「杭州」，否则分组会多出一组"],
            ["空值识别", "空串、\"-\"、\"N/A\"、\"无\" 统一转为真正的空值"],
            ["数字清洗", "\"1,234.50\"、\"￥2000\"、\"2 000 元\" → 1234.5 / 2000"],
            ["日期识别", "2025-01-05、2025/01/05、2025年1月5日 统一成日期类型"],
            ["整行去重", "重复导出的行只保留一条"],
            ["删除空行", "表格末尾那些只有逗号的行"],
            ["表头规范化", "「销售额 (元)」→「销售额_元」，避免特殊字符"],
        ],
        [4.2, 12.0],
    )
    body(doc, "导入完成后系统会重建数据字典，新表**立刻**可以被提问，不需要重启或重新配置。")

    h2(doc, "5. 看得见进度：答案还没出来，你已经知道它在干什么")
    body(doc, "一次问数要调用大模型，冷启动大约 1.3 秒。传统做法是这段时间里界面只有一行「处理中」——"
              "用户不知道系统是不是卡死了。askdata 改成把每个阶段实时推送出来：")
    table(
        doc,
        [
            ["时间", "发生什么", "用户看到"],
            ["15 毫秒", "连接建立", "界面立刻响应"],
            ["16 毫秒", "表召回完成", "「已从 67 张表里选出 8 张」"],
            ["1.29 秒", "模型生成 SQL 完成", "「正在校验语句安全性」"],
            ["1.34 秒", "执行 + 结果校验完成", "答案出现"],
        ],
        [3.4, 6.4, 6.4],
    )
    body(doc, "**首字节 15 毫秒，完整结果 1.34 秒。** 等待时间没有缩短，但这一秒多里用户一直有东西可看，"
              "体感完全不同——这也是内部试用时反馈最直接的改进。")

    h2(doc, "6. 守得住边界：谁可以查、能查到哪些数据")
    body(doc, "系统默认不鉴权（便于内网试用），但两个开关可以把它变成受控系统：")
    bullet(doc, "开启后所有接口都必须携带令牌，未授权的请求一律返回 401。", bold_prefix="访问令牌：")
    bullet(doc, "可按角色给查询自动加上数据范围条件，例如只能看某些渠道、某些省份的数据。"
                "改写发生在执行之前，不依赖模型自觉。", bold_prefix="行级权限：")
    body(doc, "另外，每次查询结果都会经过一道**结果校验**：金额超过全库总额 3 倍（典型的关联重复计数）、"
              "计数出现负数、比率超过 100%、该有数据却返回空——这四种情况会在答案上方弹出提示，"
              "避免业务拿着一个「看起来很正常」的错误数字去开会。")

    page_break(doc)

    h1(doc, "四、可验证的效果", before=0)
    body(doc, "我们不希望客户只凭演示下判断，所以把所有结论都做成了可复现的报告。")
    table(
        doc,
        [
            ["能力", "指标", "结果"],
            ["问数准确率", "50 题执行准确率（3 次重复）", "90.0% ± 2.0"],
            ["成本优化", "召回 Top-15 vs 全量注入", "提问成本降低约 70%，准确率无显著差异"],
            ["自动纠错", "首次报错后能否自愈", "语法类错误 12/12 全部修复"],
            ["安全防护", "50 条注入 + 10 条正常", "100% 拦截，0 误伤"],
            ["澄清能力", "含糊问题是否反问", "追问率 100%，误反问 0%"],
            ["响应体感", "首字节延迟（冷启动）", "15 ms（完整结果 1.34 s）"],
            ["结果可信", "异常数字自动告警", "扇出 / 负数 / 比率越界 / 空结果四类"],
            ["澄清闭环", "追问之后能否答对", "0% → 87%"],
            ["多轮对话", "追问「那 2024 年呢」", "单轮 0% → 多轮 76.7%"],
        ],
        [3.4, 6.6, 6.2],
    )
    body(doc, "需要说明的是：在 50 道题的规模上，任何小于 3 个百分点的差异都在统计噪声之内。"
              "我们在报告里明确标注了置信区间，没有把噪声包装成「提升」。"
              "这也是我们希望客户在评估任何 AI 产品时采用的同一把尺子。")

    h1(doc, "五、技术形态与部署")
    table(
        doc,
        [
            ["项目", "说明"],
            ["部署方式", "私有化部署，单机即可运行；数据不出企业内网"],
            ["依赖", "仅需 Python 运行环境与一个数据文件，无需额外的向量库、缓存或消息队列"],
            ["前端", "内置 Web 界面，零构建，断网可用；支持日间 / 夜间两种主题"],
            ["数据源", "内置 DuckDB 分析引擎，可直接读入 CSV / JSON，也可对接已有分析库"],
            ["大模型", "通过兼容 OpenAI 协议的接口调用，模型可替换，支持境内合规模型"],
        ],
        [3.4, 12.8],
    )
    body(doc, "对客户而言这意味着：不需要改造现有数仓，不需要采购额外中间件，一个技术人员半天即可完成部署与试跑。")

    h1(doc, "六、界面与使用体验")
    body(doc, "系统提供六个功能页面，覆盖从提问到评估的完整链路：")
    bullet(doc, "业务人员在这里提问，看到答案、依据的查询语句和结果表；可以复制语句、导出结果。", bold_prefix="① 问数：")
    bullet(doc, "查看全部表、字段、枚举取值和样例数据，并在这里导入自己的数据。", bold_prefix="② 数据字典：")
    bullet(doc, "准确率趋势、逐题结果、失败原因分类，随时可查。", bold_prefix="③ 评测：")
    bullet(doc, "输入任意语句，立即看到是否放行、被拦原因；可开关行级权限看改写效果。", bold_prefix="④ 安全闸：")
    bullet(doc, "展示含糊问题的追问原文与真实效果对比。", bold_prefix="⑤ 澄清：")
    bullet(doc, "项目说明、链路图与全部量化结论。", bold_prefix="⑥ 说明：")

    h1(doc, "七、适用的场景与边界")
    h2(doc, "适合")
    bullet(doc, "业务人员频繁提出重复性取数需求，数据团队被临时查询淹没的团队。")
    bullet(doc, "希望在内部先小范围验证「AI 问数」价值，再决定是否大规模投入的企业。")
    bullet(doc, "对数据安全有明确要求、不能把数据交给第三方 SaaS 的行业。")

    h2(doc, "当前版本不适合")
    body(doc, "我们倾向于把边界讲清楚，而不是把能力说得很大：")
    bullet(doc, "面向超高并发、多租户的公有云场景，需要额外做鉴权、审计与隔离。")
    bullet(doc, "跨多个异构数据库的联邦查询，当前版本聚焦单一分析库。")
    bullet(doc, "完全无人值守的自动决策——系统定位是辅助人，而不是替代人。")

    h1(doc, "八、下一步规划")
    table(
        doc,
        [
            ["方向", "内容"],
            ["评测集扩容", "从 50 题扩到 200 题以上，覆盖更多行业与问法，进一步压缩统计噪声"],
            ["多轮能力加固", "当前瓶颈在「把追问改写成完整问题」，下一步重点优化这一环节的可靠性"],
            ["权限体系", "在行级权限之上补齐用户体系、操作审计与数据脱敏"],
            ["行业模板", "针对零售、制造、金融等行业沉淀开箱即用的语义层与指标口径"],
        ],
        [3.6, 12.6],
    )

    h1(doc, "九、我们的一句话")
    quote(
        doc,
        "让 AI 报出来的数字可以被信任——不清楚就问，问清楚再算，算完能核对，"
        "而且整套判断标准经过校准、有误差范围。",
    )

    para(doc, f"askdata  ·  {date.today().isoformat()}", size=9, color=DIM,
         before=18, align=WD_ALIGN_PARAGRAPH.CENTER)

    doc.save(path)


def main() -> None:
    out = Path("docs")
    out.mkdir(parents=True, exist_ok=True)
    proposal = out / "askdata-产品介绍书-客户版.docx"
    build_proposal(proposal)
    print(f"{proposal}  {proposal.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
