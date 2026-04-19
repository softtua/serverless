import requests
from qwen_agent.tools import BaseTool

try:
    from trafilatura import extract as trafilatura_extract
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False


class FetchURLTool(BaseTool):
    name = 'fetch_url'
    description = 'Fetches and extracts the main text content from a given URL. Use this when you need to read a webpage, article or any online resource.'
    parameters = [{
        'name': 'url',
        'type': 'string',
        'description': 'The full URL to fetch and extract text from (e.g. https://example.com/article)',
        'required': True
    }]

    def call(self, params, **kwargs) -> dict:
        # qwen_agent may pass tool_args as a JSON string instead of a dict
        if isinstance(params, str):
            import json as _json
            try:
                params = _json.loads(params)
            except Exception:
                # If it's a bare URL string, wrap it
                params = {'url': params.strip()}
        url = params.get('url', '').strip()
        if not url:
            return {'error': 'URL is required'}

        try:
            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                )
            }
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            if TRAFILATURA_AVAILABLE:
                text = trafilatura_extract(response.text, include_links=True) or ''
            else:
                # Very basic fallback – strip tags
                import re
                text = re.sub(r'<[^>]+>', ' ', response.text)
                text = re.sub(r'\s+', ' ', text).strip()

            # Limit to ~12 000 chars (~3-4k tokens) to avoid context overflow
            max_chars = 12_000
            truncated = False
            if len(text) > max_chars:
                text = text[:max_chars]
                truncated = True

            return {
                'url': url,
                'text': text,
                'char_count': len(text),
                'truncated': truncated,
            }

        except requests.RequestException as exc:
            return {'error': f'Failed to fetch URL: {exc}'}
        except Exception as exc:
            return {'error': f'Extraction error: {exc}'}

