from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

objStyles = getSampleStyleSheet()

for strName, objStyle in objStyles.byName.items():
  if isinstance(objStyle, ParagraphStyle):
    print("{}: fontName={}, fontSize={}, leading={}, alignment={},  "
          "leftIndent={}, rightIndent={}".format(
      strName,
      objStyle.fontName,
      objStyle.fontSize,
      objStyle.leading,
      objStyle.alignment,
      objStyle.leftIndent,
      objStyle.rightIndent
    ))