import aiohttp
import asyncio
from bs4 import BeautifulSoup
from .exceptions import *


def stdout_encode(u, default='UTF8'):
    encoding = sys.stdout.encoding or default
    if sys.version_info > (3, 0):
        return u.encode(encoding).decode(encoding)
    return u.encode(encoding)


class PageError(Exception):
    pass


class WikiPage:
    def __init__(self, *,
                 title: str=None,
                 page_id=None,
                 redirect=True,
                 preload=False,
                 original_title='',
                 locale='en',
                 session=aiohttp.ClientSession()):
        if title:
            self.title = title
            self.original_title = original_title
        elif page_id:
            self.page_id = page_id
        else:
            raise ValueError("Either a Title or a Page ID myst be specified.")

        self.loop = asyncio.get_event_loop()
        self.session = session
        self.endpoint = "https://{}.wikipedia.org/w/api.php?action=query&format=json".format(locale)
        asyncio.run_coroutine_threadsafe(self.__load(redirect, preload), self.loop)

        if preload:
            for prop in ('content', 'summary', 'images', 'references', 'links', 'sections'):
                getattr(self, prop)

    def __repr__(self):
        return stdout_encode(u'<WikipediaPage \'{}\'>'.format(self.title))

    def __eq__(self, other):
        try:
            return (
                self.page_id == other.page_id
                and self.title == other.title
            )
        except:
            return False

    async def __load(self, redirect=True, preload=False):
        url = f"{self.endpoint}&prop=info|pageprops&inprop=url&ppprop=disambiguation&redirects= "
        url += f"&titles={self.title}" if not getattr(self, 'page_id', None) else f"&pageids={self.page_id}"
        async with self.session.get(url) as r:
            result = r.json()
        query = result['query']
        page_id = list(query['pages'].keys())[0]
        page = query['pages'][page_id]

        if 'missing' in page:
            if hasattr(self, 'title'):
                raise PageError(self.title)
            else:
                raise PageError(self.page_id)
        elif 'redirects' in query:
            if redirect:
                redirects = query['redirects'][0]

                if 'normalized' in query:
                    normalized = query['normalized'][0]
                    assert normalized['from'] == self.title, ODD_ERROR_MESSAGE

                    from_title = normalized['to']

                else:
                    from_title = self.title

                assert redirects['from'] == from_title, ODD_ERROR_MESSAGE

                self.__init__(title=redirects['to'], redirect=redirect, preload=preload)

            else:
                raise RedirectError(getattr(self, 'title', page['title']))
        elif 'pageprops' in page:
            url = f"{self.endpoint}&prop=revisions&rvprop=content&rvparse=&rvlimit=1"
            url += f'&pageids={self.page_id}' if hasattr(self, 'page_id') else f'&titles={self.title}'
            async with self.session.get(url) as r:
                result = r.json()
            html = result['query']['pages'][page_id]['revisions'][0]['*']

            lis = BeautifulSoup(html, 'html.parser').find_all('li')
            filtered_lis = [li for li in lis if 'tocsection' not in ''.join(li.get('class', []))]
            may_refer_to = [li.a.get_text() for li in filtered_lis if li.a]

            raise DisambiguationError(getattr(self, 'title', page['title']), may_refer_to)
        else:
            self.page_id = page_id
            self.title = page['title']
            self.url = page['fullurl']

    async def continued_query(self, query_params):
        query_params.update(self.__title_query_param)

        last_continue = {}

        prop = query_params.get('prop', None)

        while True:
            params = query_params.copy()
            params.update(last_continue)

            async with self.session.get(self.endpoint, params=params) as r:
                result = r.json()

            if 'query' not in result:
                break

            pages = result['query']['pages']
            if 'generator' in query_params:
                for datum in pages.values():  # in python 3.3+: "yield from pages.values()"
                    yield datum
            else:
                for datum in pages[self.page_id][prop]:
                    yield datum

            if 'continue' not in result:
                break

            last_continue = result['continue']

    @property
    def __title_query_param(self):
        if getattr(self, 'title', None) is not None:
            return {'titles': self.title}
        else:
            return {'pageids': self.page_id}

    async def html(self):
        '''
        Get full page HTML.
        .. warning:: This can get pretty slow on long pages.
        '''

        if not getattr(self, '_html', False):
            params = {
                'prop': 'revisions',
                'rvprop': 'content',
                'rvlimit': 1,
                'rvparse': '',
                'titles': self.title
            }

            request = (await (await self.session.get(self.endpoint, params=params)).json())
            self._html = request['query']['pages'][self.page_id]['revisions'][0]['*']

        return self._html

    @property
    async def content(self):
        """
        Plain text content of the page, excluding images, tables, and other data.
        """

        if not getattr(self, '_content', False):
            params = {
                'prop': 'extracts|revisions',
                'explaintext': '',
                'rvprop': 'ids'
            }
            if not getattr(self, 'title', None) is None:
                params['titles'] = self.title
            else:
                params['pageids'] = self.page_id
            request = (await (await self.session.get(self.endpoint, params=params)).json())
            self._content = request['query']['pages'][self.page_id]['extract']
            self._revision_id = request['query']['pages'][self.page_id]['revisions'][0]['revid']
            self._parent_id = request['query']['pages'][self.page_id]['revisions'][0]['parentid']

        return self._content

    @property
    def revision_id(self):
        """
        Revision ID of the page.
        The revision ID is a number that uniquely identifies the current
        version of the page. It can be used to create the permalink or for
        other direct API calls. See `Help:Page history
        <http://en.wikipedia.org/wiki/Wikipedia:Revision>`_ for more
        information.
        """

        if not getattr(self, '_revid', False):
            # fetch the content (side effect is loading the revid)
            asyncio.run_coroutine_threadsafe(self.content, self.loop)

        return self._revision_id

    @property
    def parent_id(self):
        """
        Revision ID of the parent version of the current revision of this
        page. See ``revision_id`` for more information.
        """

        if not getattr(self, '_parentid', False):
            # fetch the content (side effect is loading the revid)
            asyncio.run_coroutine_threadsafe(self.content, self.loop)

        return self._parent_id

    @property
    async def summary(self):
        """
        Plain text summary of the page.
        """

        if not getattr(self, '_summary', False):
            params = {
                'prop': 'extracts',
                'explaintext': '',
                'exintro': '',
            }
            if not getattr(self, 'title', None) is None:
                params['titles'] = self.title
            else:
                params['pageids'] = self.page_id

            request = (await (await self.session.get(self.endpoint, params=params)).json())
            self._summary = request['query']['pages'][self.page_id]['extract']

        return self._summary

    async def section(self, section_title):
        """
        Get the plain text content of a section from `self.sections`.
        Returns None if `section_title` isn't found, otherwise returns a whitespace stripped string.
        This is a convenience method that wraps self.content.
        .. warning:: Calling `section` on a section that has subheadings will NOT return
               the full text of all of the subsections. It only gets the text between
               `section_title` and the next subheading, which is often empty.
        """

        section = u"== {} ==".format(section_title)
        try:
            index = (await self.content).index(section) + len(section)
        except ValueError:
            return None

        try:
            next_index = (await self.content).index("==", index)
        except ValueError:
            next_index = len((await self.content))

        return (await self.content)[index:next_index].lstrip("=").strip()


class AIOPedia:
    def __init__(self, title: str=None, *, locale: str='en', results: int=1):
        self.max_results = results
        self.title = title.replace(' ', '%20')
        self.user_agent = "AIOPedia (https://github.com/NCPlayz/AIOPedia/)"
        self.endpoint = "https://{}.wikipedia.org/w/api.php?action=query&format=json".format(locale)
        self.locale = locale
        self.loop = asyncio.get_event_loop()
        self.session = aiohttp.ClientSession()

    async def get_summary(self):
        request = f"{self.endpoint}&srsearch={self.title}&list=search&srlimit={self.max_results}"
        async with self.session.get(request) as r:
            result = await r.json()
        snippet = result['query']['search'][0]['snippet']
        cleantext = BeautifulSoup(snippet, "lxml").text
        return cleantext + '...'

    async def page(self, *, page_id: int=None, auto_suggest=True, redirect=True, preload=False):
        if self.title:
            if auto_suggest:
                results, suggestion = (await self.search(suggestion=True))
                try:
                    self.title = suggestion or results[0]
                except IndexError:
                    raise PageError(f"{self.title} does not exist.")
            return WikiPage(title=self.title, locale=self.locale, session=self.session,
                            redirect=redirect, preload=preload)
        elif page_id:
            return WikiPage(page_id=page_id, locale=self.locale, session=self.session,
                            redirect=redirect, preload=preload)
        else:
            raise ValueError("Either a Title or a Page ID must be specified.")

    async def search(self, results=10, suggestion=False):
        """
        Do a Wikipedia search for `query`.
        Keyword arguments:
        * results - the maxmimum number of results returned
        * suggestion - if True, return results and suggestion (if any) in a tuple
        """

        params = {
            'list': 'search',
            'srprop': '',
            'srlimit': results,
            'limit': results,
            'srsearch': self.title
        }
        if suggestion:
            params['srinfo'] = 'suggestion'

        raw_results = (await (await self.session.get(self.endpoint, params=params)).json())

        if 'error' in raw_results:
            if raw_results['error']['info'] in ('HTTP request timed out.', 'Pool queue is full'):
                raise HTTPTimeoutError(self.title)
            else:
                raise WikipediaException(raw_results['error']['info'])

        search_results = (d['title'] for d in raw_results['query']['search'])

        if suggestion:
            if raw_results['query'].get('searchinfo'):
                return list(search_results), raw_results['query']['searchinfo']['suggestion']
            else:
                return list(search_results), None

        return list(search_results)
