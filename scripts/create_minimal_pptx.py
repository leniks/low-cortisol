from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import html


OUT = Path("docs/ai_agent_for_economists.pptx")

SLIDE_W = 13_333_333
SLIDE_H = 7_500_000


def xml_escape(value: str) -> str:
    return html.escape(value, quote=True)


def text_box(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    text: str,
    size: int,
    color: str,
    bold: bool = False,
    align: str = "l",
) -> str:
    bold_attr = ' b="1"' if bold else ""
    return f"""<p:sp>
  <p:nvSpPr><p:cNvPr id="{shape_id}" name="{xml_escape(name)}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
  <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>
  <p:txBody>
    <a:bodyPr wrap="square" rtlCol="0"/>
    <a:lstStyle/>
    <a:p>
      <a:pPr algn="{align}"/>
      <a:r><a:rPr lang="ru-RU" sz="{size}"{bold_attr}><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Aptos Display"/><a:cs typeface="Aptos Display"/></a:rPr><a:t>{xml_escape(text)}</a:t></a:r>
      <a:endParaRPr lang="ru-RU" sz="{size}"/>
    </a:p>
  </p:txBody>
</p:sp>"""


def rect(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    fill: str,
    transparency: int = 0,
    radius: str = "rect",
) -> str:
    alpha = f"<a:alpha val=\"{100000 - transparency}\"/>" if transparency else ""
    return f"""<p:sp>
  <p:nvSpPr><p:cNvPr id="{shape_id}" name="{xml_escape(name)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
  <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="{radius}"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="{fill}">{alpha}</a:srgbClr></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>
</p:sp>"""


def line(shape_id: int, name: str, x1: int, y1: int, x2: int, y2: int, color: str, width: int) -> str:
    return f"""<p:cxnSp>
  <p:nvCxnSpPr><p:cNvPr id="{shape_id}" name="{xml_escape(name)}"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>
  <p:spPr><a:xfrm><a:off x="{x1}" y="{y1}"/><a:ext cx="{x2 - x1}" cy="{y2 - y1}"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom><a:ln w="{width}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></p:spPr>
</p:cxnSp>"""


SLIDE = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{SLIDE_W}" cy="{SLIDE_H}"/><a:chOff x="0" y="0"/><a:chExt cx="{SLIDE_W}" cy="{SLIDE_H}"/></a:xfrm></p:grpSpPr>
      {rect(2, "Top accent", 0, 0, SLIDE_W, 170_000, "0B5CAD")}
      {rect(3, "Soft blue block", 8_760_000, 0, 4_573_333, SLIDE_H, "D9ECFF", 23000)}
      {rect(4, "Deep blue block", 11_900_000, 0, 1_433_333, SLIDE_H, "0B5CAD", 0)}
      {rect(5, "Light circle", 9_050_000, 1_020_000, 2_050_000, 2_050_000, "BFE2FF", 26000, "ellipse")}
      {rect(6, "Outline circle", 9_950_000, 4_900_000, 1_270_000, 1_270_000, "7DBAF2", 45000, "ellipse")}
      {line(7, "Divider", 950_000, 4_470_000, 6_150_000, 4_470_000, "42A5F5", 19050)}
      {text_box(8, "Label", 950_000, 1_190_000, 3_000_000, 360_000, "Название темы", 1600, "42A5F5", True)}
      {text_box(9, "Title", 940_000, 1_680_000, 7_500_000, 1_650_000, "ИИ-агент для экономистов", 4600, "0A2540", True)}
      {text_box(10, "Team label", 950_000, 4_740_000, 1_500_000, 320_000, "Команда", 1500, "64748B", True)}
      {text_box(11, "Team", 950_000, 5_110_000, 4_100_000, 600_000, "Норм челики", 2500, "0B5CAD", True)}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


FILES = {
    "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
    "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
    "docProps/app.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat><Slides>1</Slides>
</Properties>""",
    "docProps/core.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>ИИ-агент для экономистов</dc:title><dc:creator>Codex</dc:creator><cp:lastModifiedBy>Codex</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">2026-05-11T00:00:00Z</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">2026-05-11T00:00:00Z</dcterms:modified>
</cp:coreProperties>""",
    "ppt/presentation.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
  <p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}" type="wide"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>""",
    "ppt/_rels/presentation.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>
</Relationships>""",
    "ppt/slides/slide1.xml": SLIDE,
    "ppt/slides/_rels/slide1.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>""",
    "ppt/slideMasters/slideMaster1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
  <p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>
</p:sldMaster>""",
    "ppt/slideMasters/_rels/slideMaster1.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>""",
    "ppt/slideLayouts/slideLayout1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">
  <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>""",
    "ppt/slideLayouts/_rels/slideLayout1.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>""",
    "ppt/theme/theme1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Minimal Blue">
  <a:themeElements>
    <a:clrScheme name="Minimal Blue"><a:dk1><a:srgbClr val="0A2540"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1E293B"/></a:dk2><a:lt2><a:srgbClr val="EFF6FF"/></a:lt2><a:accent1><a:srgbClr val="0B5CAD"/></a:accent1><a:accent2><a:srgbClr val="42A5F5"/></a:accent2><a:accent3><a:srgbClr val="7DBAF2"/></a:accent3><a:accent4><a:srgbClr val="BFE2FF"/></a:accent4><a:accent5><a:srgbClr val="64748B"/></a:accent5><a:accent6><a:srgbClr val="D9ECFF"/></a:accent6><a:hlink><a:srgbClr val="0B5CAD"/></a:hlink><a:folHlink><a:srgbClr val="64748B"/></a:folHlink></a:clrScheme>
    <a:fontScheme name="Aptos"><a:majorFont><a:latin typeface="Aptos Display"/><a:ea typeface=""/><a:cs typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/><a:ea typeface=""/><a:cs typeface="Aptos"/></a:minorFont></a:fontScheme>
    <a:fmtScheme name="Minimal"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
  </a:themeElements>
</a:theme>""",
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUT, "w", ZIP_DEFLATED) as pptx:
        for name, content in FILES.items():
            pptx.writestr(name, content)
    print(OUT)


if __name__ == "__main__":
    main()
