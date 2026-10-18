import base64
import io
import json
import threading
import unittest
from myinvoices.provider_documents import resolve_document, ResolutionError
import test_pdf_downloads as pdf_helpers
from test_pdf_downloads import Response
from myinvoices.pdf_downloads import PDFDownloader


class ResolverTests(unittest.TestCase):
    def test_explicit_embed_json_and_pdfjs_links(self):
        base = 'https://provider.example/p/SYNTHETIC'
        for body,mime,expected in [
            (b'<html><iframe src="/files/invoice.pdf?token=synthetic&amp;view=1"></iframe></html>','text/html','https://provider.example/files/invoice.pdf?token=synthetic&view=1'),
            (b'<html><object data="/document" type="application/pdf"></object></html>','text/html','https://provider.example/document'),
            (b'<html><embed src="/pdfjs/web/viewer.html?file=%2Fdocuments%2F1"></html>','text/html','https://provider.example/documents/1'),
            (b'{"document":{"pdfUrl":"/api/document"}}','application/json','https://provider.example/api/document'),
            (b'<html><script type="application/json">{"pdf_url":"/doc"}</script></html>','text/html','https://provider.example/doc'),
        ]:
            self.assertEqual(resolve_document(body,base,mime),expected)

    def test_does_not_guess_or_follow_arbitrary_links(self):
        for body,state in [(b'<html><a href="/terms.pdf">Terms</a></html>','html_page'),
            (b'<html><embed src="/one"><embed src="/two"></html>','ambiguous_document'),
            (b'{"url":"/unknown"}','html_page'),(b'{bad json','invalid_descriptor')]:
            with self.assertRaises(ResolutionError) as error:
                resolve_document(body,'https://provider.example/p')
            self.assertEqual(error.exception.state,state)
        result=resolve_document(b'<html><embed hidden src="/wrong"><embed src="/right"></html>','https://provider.example/p')
        self.assertEqual(result,'https://provider.example/right')

    def test_inline_base64_and_limits(self):
        encoded=base64.b64encode(b'%PDF-synthetic').decode()
        self.assertEqual(resolve_document(json.dumps({'pdfBase64':encoded}).encode(),'https://provider.example/p','application/json'),b'%PDF-synthetic')
        with self.assertRaises(ResolutionError):
            resolve_document(b'['*1000,'https://provider.example/p','application/json')


class ProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        pdf_helpers.PDFTests.setUp(self)

    def test_html_then_json_then_pdf(self):
        responses=[]
        class Scripted:
            def __init__(other): other.urls=[]
            def open(other,request,timeout):
                other.urls.append(request.full_url)
                if len(other.urls)%3==1: body=b'<html><iframe src="/api/doc"></iframe></html>'
                elif len(other.urls)%3==2: body=b'{"pdfUrl":"/files/invoice"}'
                else: body=self.pdf
                response=Response(body);responses.append(response)
                return response
        opener=Scripted()
        result=PDFDownloader(self.db,opener).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],2)
        self.assertEqual(opener.urls[:3],['https://provider.example/pdf','https://provider.example/api/doc','https://provider.example/files/invoice'])
        self.assertTrue(all(response.closed for response in responses))
        self.assertFalse(list(self.root.rglob('*.part')))


    def test_data_pdf_descriptor(self):
        from test_pdf_downloads import Opener
        body=json.dumps({'pdfBase64':base64.b64encode(self.pdf).decode()}).encode()
        result=PDFDownloader(self.db,Opener(body)).run(self.client,self.root/'archive',threading.Event())
        self.assertEqual(result['saved'],2)
