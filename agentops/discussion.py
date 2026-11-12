"""Meaningful discussion identity, with narrow causal-publication exclusions."""

from .model import digest


def fingerprint(pr, omitted=()):
    known = {(entry['kind'], entry['id']): entry for entry in omitted}
    def actor(item):
        author = item.get('author') or item.get('user') or {}
        return author.get('login', 'ghost') if isinstance(author, dict) else author
    def normalize(item):
        return {**{key: item[key] for key in ('id', 'body', 'state', 'commit_id', 'path', 'line',
                    'original_line', 'diff_hunk', 'in_reply_to_id') if key in item}, 'author': actor(item)}
    def keep(item, kind):
        entry = known.get((kind, item.get('id')))
        if not entry or entry['body_hash'] != digest(item.get('body') or ''):
            return True
        return kind == 'review' and item.get('state') != entry.get('state')
    value = {'title': pr.get('title') or '', 'body': pr.get('body') or '',
             'comments': [normalize(c) for c in pr.get('comments', []) if keep(c, 'comment')],
             'reviews': [normalize(r) for r in pr.get('reviews', []) if keep(r, 'review')],
             'review_comments': [normalize(c) for c in pr.get('review_comments', [])],
             'threads': [{**{k: t[k] for k in ('id', 'isResolved', 'isOutdated', 'path', 'line') if k in t},
                          'comments': [normalize(c) for c in t.get('comments', [])]}
                         for t in pr.get('threads', [])]}
    return digest(value)
