"""Public routes with read-only transactions and existing application errors."""
from contextlib import contextmanager
from urllib.parse import urlencode
from fastapi import Path, Request

from app.web import creatures, queries
from app.web.creature_presentation import presentation


def register_creature_routes(application, render, status):
    @contextmanager
    def read():
        with application.state.engine.connect() as connection:
            connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            yield connection

    @application.get('/api/creatures')
    def api_creatures(request: Request):
        filters = creatures.parse_filters(request.query_params)
        with read() as connection:
            return creatures.search(connection, filters)

    @application.get('/api/creatures/{creature_id}')
    def api_creature(request: Request, creature_id: int = Path(ge=1, le=9223372036854775807)):
        if request.query_params:
            queries.invalid('This endpoint does not accept query parameters')
        with read() as connection:
            return creatures.detail(connection, creature_id)

    @application.get('/api/creatures/{creature_id}/resources')
    def api_creature_resources(request: Request, creature_id: int = Path(ge=1, le=9223372036854775807)):
        filters = creatures.parse_filters(request.query_params, mode='resources')
        with read() as connection:
            return creatures.resources(connection, creatures.detail(connection, creature_id), filters, status(connection))

    @application.get('/api/creatures/{creature_id}/spawn-evidence')
    def api_spawn_evidence(request: Request, creature_id: int = Path(ge=1, le=9223372036854775807)):
        filters = creatures.parse_filters(request.query_params, mode='evidence')
        with read() as connection:
            creature = creatures.detail(connection, creature_id)
            return {**creatures.spawn_evidence(connection, creature, filters),
                    'creature_id': creature_id, 'revision': creature['revision'],
                    'location_warnings': creature['location_warnings']}

    @application.get('/api/resources/{resource_id}/creatures')
    def api_resource_creatures(request: Request, resource_id: int = Path(ge=1, le=9223372036854775807)):
        filters = creatures.parse_filters(request.query_params, mode='reverse')
        with read() as connection:
            current = status(connection)
            queries.resource_detail(connection, resource_id, current)
            return {**creatures.reverse_lookup(connection, resource_id, filters, current), 'resource_id': resource_id}

    @application.get('/creatures')
    def directory(request: Request):
        filters = creatures.parse_filters(request.query_params)
        with read() as connection:
            data = creatures.search(connection, filters)
            return render(request, 'creatures.html', {'title': 'Creature Harvesting Directory', 'status': status(connection),
                'display': presentation(connection, data['items']),
                'data': data, 'filters': filters, 'references': creatures.catalog_references(connection),
                'pagination': creatures.page_links(request, data)})

    @application.get('/creatures/{creature_id}')
    def creature_page(request: Request, creature_id: int = Path(ge=1, le=9223372036854775807)):
        filters = creatures.parse_filters(request.query_params, mode='detail')
        with read() as connection:
            creatures.validate_planet(connection, filters.get('planet'))
            creature = creatures.detail(connection, creature_id)
            current = status(connection)
            evidence = creatures.spawn_evidence(connection, creature, filters)
            # Evidence and resource lists have independent pagination.
            matches = creatures.resources(connection, creature, {**filters, 'page': filters['resource_page']}, current) if filters.get('planet') else None
            resource_pagination = {}
            if matches:
                for category in matches['categories']:
                    links = {}
                    for label, number in (('previous', category['page']-1), ('next', category['page']+1)):
                        links[label] = request.url.path + '?' + urlencode({**dict(request.query_params),
                            'category': category['category'], 'resource_page': number}) if 1 <= number <= category['pages'] else None
                    resource_pagination[category['category']] = links
            display = presentation(connection, [creature])
            return render(request, 'creature_detail.html', {'title': display.creature(creature), 'display': display, 'status': current, 'creature': creature,
                'evidence': evidence, 'resource_matches': matches, 'selected_planet': filters.get('planet', ''),
                'selected_category': filters.get('category', ''), 'references': creatures.catalog_references(connection),
                'pagination': creatures.page_links(request, evidence), 'resource_pagination': resource_pagination})

    @application.get('/resources/{resource_id}/creatures')
    def resource_creature_page(request: Request, resource_id: int = Path(ge=1, le=9223372036854775807)):
        filters = creatures.parse_filters(request.query_params, mode='reverse')
        with read() as connection:
            current = status(connection)
            resource = queries.resource_detail(connection, resource_id, current)
            data = creatures.reverse_lookup(connection, resource_id, filters, current)
            return render(request, 'resource_creatures.html', {'title': 'Creatures for ' + resource['name'], 'status': current,
                'display': presentation(connection, data['items']),
                'resource': resource, 'data': data, 'filters': filters, 'references': creatures.catalog_references(connection),
                'pagination': creatures.page_links(request, data)})
