"""Web search for the assistant (site/docs/admin/web-search.md).

- adapters.py: one small class per kind of search service (Exa, Brave, Tavily, degoog, SearXNG);
- service.py: the providers an admin registered, tried in priority order with limits, timeouts
  and fallback, each reached by the route its network allows (egress proxy, direct, or the LAN);
- pages.py: reading one result page as plain text, under the same egress rules as content from mail;
- routes.py: Admin → Web search (providers, test).

The agent tools (`web_search`, `read_page`) are in agent/web_tools.py.
"""
