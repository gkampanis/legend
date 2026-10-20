"""Minimal streaming XLSX audit report; identity cells are literal strings."""
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile,ZIP_DEFLATED
from ..models import ApplicationError

CT='''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'''
ROOT_RELS='''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
WB='''<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Ιστορικό Entersoft" sheetId="1" r:id="rId1"/></sheets></workbook>'''
WB_RELS='''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'''


def write_excel(path,headers,rows):
    path=Path(path);created=False
    def letters(index):
        result=''
        while index:
            index,remainder=divmod(index-1,26);result=chr(65+remainder)+result
        return result
    try:
        with ZipFile(path,'x',ZIP_DEFLATED) as archive:
            created=True
            for name,data in [('[Content_Types].xml',CT),('_rels/.rels',ROOT_RELS),('xl/workbook.xml',WB),('xl/_rels/workbook.xml.rels',WB_RELS)]:archive.writestr(name,data)
            with archive.open('xl/worksheets/sheet1.xml','w') as sheet:
                sheet.write(b'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><sheetData>')
                def emit(index,values):
                    cells=[]
                    for column,value in enumerate(values,1):
                        text=''.join(c for c in str(value) if ord(c)>=32 or c in '\t\n\r')
                        cells.append(f'<c r="{letters(column)}{index}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
                    sheet.write((f'<row r="{index}">'+''.join(cells)+'</row>').encode())
                emit(1,headers);count=0
                for count,row in enumerate(rows,1):
                    if count>=1048576:raise ApplicationError('Το ιστορικό υπερβαίνει το όριο γραμμών Excel. Επιλέξτε CSV.')
                    emit(count+1,row)
                sheet.write((f'</sheetData><autoFilter ref="A1:{letters(len(headers))}{count+1}"/></worksheet>').encode())
        return count
    except (OSError,ApplicationError):
        if created:path.unlink(missing_ok=True)
        raise ApplicationError('Δεν γράφτηκε το Excel. Επιλέξτε νέο αρχείο και διαθέσιμο φάκελο.') from None
    except Exception:
        if created:path.unlink(missing_ok=True)
        raise
