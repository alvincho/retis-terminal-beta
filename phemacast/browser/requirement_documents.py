"""Opaque requirement traversal; semantic model adoption belongs to publishers."""
from copy import deepcopy


def map_requirements(entry, transform):
    result = deepcopy(entry)
    design = result.get('design')
    if isinstance(design, dict):
        design['dataRequirements'] = [transform(deepcopy(r)) for r in design.get('dataRequirements', [])]
    def visit(value):
        if isinstance(value, dict):
            presentation = value.get('presentation')
            if isinstance(presentation, dict) and isinstance(presentation.get('document'), dict):
                document = presentation['document']
                if isinstance(document.get('design'), dict):
                    document['design']['dataRequirements'] = [transform(deepcopy(r)) for r in document['design'].get('dataRequirements', [])]
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    for component in result.get('composition', {}).get('components', []): visit(component)
    return result
