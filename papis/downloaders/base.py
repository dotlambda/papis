import os
import logging
import requests
import papis.config
import papis.document
import papis.utils
import filetype
import papis.importer
import papis.bibtex
import tempfile
import copy
import re


meta_equivalences = {
    "og:type": "type",
    "og:title": "title",
    "og:url": "url",
    "description": "abstract",
    "citation_doi": "doi",
    "citation_firstpage": "firstpage",
    "citation_lastpage": "lastpage",
    "citation_fulltext_html_url": "url",
    "citation_pdf_url": "pdf_url",
    "citation_issn": "issn",
    "citation_issue": "issue",
    "citation_journal_abbrev": "journal_abbrev",
    "citation_journal_title": "journal",
    "citation_language": "language",
    "citation_online_date": "online_date",
    "citation_publication_date": "publication_date",
    "citation_publisher": "publisher",
    "citation_title": "title",
    "citation_volume": "volume",
    "dc.publisher": "publisher",
    "dc.date": "date",
    "dc.language": "language",
    "dc.subject": "subject",
    "dc.title": "title",
    "keywords": "keywords",
    "dc.type": "type",
    "dc.description": "description",
}


def parse_meta_headers(soup, extra_equivalences=dict()):
    equivalences = copy.copy(meta_equivalences)
    equivalences.update(extra_equivalences)
    metas = soup.find_all(name="meta")
    data = dict()
    for meta in metas:
        _mname = meta.attrs.get('name') or meta.attrs.get('property')
        if _mname and _mname.lower() in equivalences:
            data[equivalences[_mname.lower()]] = meta.attrs.get('content')

    author_list = parse_meta_authors(soup)
    if author_list:
        data['author_list'] = author_list
        data['author'] = papis.document.author_list_to_author(data)

    return data


def parse_meta_authors(soup):
    author_list = []
    authors = soup.find_all(name='meta', attrs={'name': 'citation_author'})
    affs = soup.find_all(name='meta',
            attrs={'name': 'citation_author_institution'})
    if affs and authors:
        tuples = zip(authors, affs)
    elif authors:
        tuples = [(a, None) for a in authors]
    else:
        return []

    for t in tuples:
        fullname = t[0].get('content')
        affiliation = [dict(name=t[1].get('content'))] if t[1] else []
        fullnames = re.split('\s+', fullname)
        author_list.append(dict(
            given=fullnames[0],
            family=' '.join(fullnames[1:]),
            affiliation=affiliation,
        ))
    return author_list


class Downloader(papis.importer.Importer):

    """This is the base class for every downloader.
    """

    def __init__(self, uri="", name="", ctx=None):
        self.ctx = ctx or papis.importer.Context()
        assert(isinstance(uri, str))
        assert(isinstance(name, str))
        assert(isinstance(self.ctx, papis.importer.Context))
        self.uri = uri
        self.name = name or os.path.basename(__file__)
        self.logger = logging.getLogger("downloader:"+self.name)
        self.logger.debug("uri {0}".format(uri))
        self.expected_document_extension = None
        self.priority = 1

        self.bibtex_data = None
        self.document_data = None

        self.session = requests.Session()
        self.session.headers = {
            'User-Agent': papis.config.get('user-agent')
        }
        proxy = papis.config.get('downloader-proxy')
        if proxy is not None:
            self.session.proxies = {
                'http': proxy,
                'https': proxy,
            }
        self.cookies = {}

    def fetch(self):
        """
        Try first to get data by hand with the get_data command.
        Then commplement with bibtex data.
        At last try to get the document from the retrieved data.
        """
        # Try with get_data
        try:
            data = self.get_data()
            assert(isinstance(data, dict))
        except NotImplementedError:
            pass
        else:
            self.ctx.data.update(data)

        # try with bibtex
        try:
            self.download_bibtex()
        except NotImplementedError:
            pass
        else:
            bib_rawdata = self.get_bibtex_data()
            if bib_rawdata:
                datalist = papis.bibtex.bibtex_to_dict(bib_rawdata)
                if datalist:
                    self.ctx.data.update(datalist[0])
        # try getting doi
        try:
            doi = self.get_doi()
        except NotImplementedError:
            pass
        else:
            self.ctx.data['doi'] = doi

        # get documents
        try:
            self.download_document()
        except NotImplementedError:
            pass
        else:
            doc_rawdata = self.get_document_data()
            if doc_rawdata and self.check_document_format():
                tmp = tempfile.mktemp()
                self.logger.info("Saving downloaded file in {0}".format(tmp))
                with open(tmp, 'wb+') as fd:
                    fd.write(doc_rawdata)
                self.ctx.files.append(tmp)

    def _get_body(self):
        """Get body of the uri, this is also important for unittesting"""
        return (self.session
                .get(self.uri)
                .content
                .decode('utf-8', errors='ignore'))

    def __str__(self):
        return 'Downloader({0}, uri={1})'.format(self.name, self.uri)

    def get_bibtex_url(self):
        """It returns the urls that is to be access to download
        the bibtex information. It has to be implemented for every
        downloader, or otherwise it will raise an exception.

        :returns: Bibtex url
        :rtype:  str
        """
        raise NotImplementedError(
            "Getting bibtex url not implemented for this downloader"
        )

    def get_bibtex_data(self):
        """Get the bibtex_data data if it has been downloaded already
        and if not download it and return the data in utf-8 format.

        :returns: Bibtex data in utf-8 format
        :rtype:  str
        """
        if not self.bibtex_data:
            self.download_bibtex()
        return self.bibtex_data

    def download_bibtex(self):
        """Bibtex downloader, it should try to download bibtex information from
        the url provided by ``get_bibtex_url``.

        It sets the ``bibtex_data`` attribute if it succeeds.

        :returns: Nothing
        :rtype:  None
        """
        url = self.get_bibtex_url()
        if not url:
            return False
        res = self.session.get(url, cookies=self.cookies)
        self.logger.info("downloading bibtex from {0}".format(url))
        self.bibtex_data = res.content.decode()

    def get_document_url(self):
        """It returns the urls that is to be access to download
        the document information. It has to be implemented for every
        downloader, or otherwise it will raise an exception.

        :returns: Document url
        :rtype:  str
        """
        raise NotImplementedError(
            "Getting bibtex url not implemented for this downloader"
        )

    def get_data(self):
        """A general data retriever, for instance when data needn't need
        to come from a bibtex
        """
        raise NotImplementedError(
            "Getting general data is not implemented for this downloader"
        )

    def get_doi(self):
        """It returns the doi of the document, if it is retrievable.
        It has to be implemented for every downloader, or otherwise it will
        raise an exception.

        :returns: Document doi
        :rtype:  str
        """
        raise NotImplementedError(
            "Getting document url not implemented for this downloader"
        )

    def get_document_data(self):
        """Get the document_data data if it has been downloaded already
        and if not download it and return the data in binary format.

        :returns: Document data in binary format
        :rtype:  str
        """
        if not self.document_data:
            self.download_document()
        return self.document_data

    def download_document(self):
        """Document downloader, it should try to download document information from
        the url provided by ``get_document_url``.

        It sets the ``document_data`` attribute if it succeeds.

        :returns: Nothing
        :rtype:  None
        """
        url = self.get_document_url()
        if not url:
            return False
        self.logger.info("downloading file from {0}".format(url))
        res = self.session.get(url, cookies=self.cookies)
        self.document_data = res.content

    def check_document_format(self):
        """Check if the downloaded document has the filetype that the
        downloader expects. If the downloader does not expect any special
        filetype, accept anything because there is no way to know if it is
        correct.

        :returns: True if it is of the right type, else otherwise
        :rtype:  bool
        """
        def print_warning():
            self.logger.error(
                "The downloaded data does not seem to be of"
                "the correct type (%s)" % self.expected_document_extension
            )

        if self.expected_document_extension is None:
            return True

        retrieved_kind = filetype.guess(self.get_document_data())

        if retrieved_kind is None:
            print_warning()
            return False

        self.logger.debug(
            'retrieved kind of document seems to be {0}'.format(
                retrieved_kind.mime)
        )

        if not isinstance(self.expected_document_extension, list):
            expected_document_extensions = [
                self.expected_document_extension
            ]

        if retrieved_kind.extension in expected_document_extensions:
            return True
        else:
            print_warning()
            return False
