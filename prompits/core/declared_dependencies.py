"""Generic legacy-Pulse dependency compilation by the selected publisher.

The host supplies the freshly authenticated original account. A declaration
does not issue authority, and typed operation parents never fall back here.
"""
from copy import deepcopy
from .capabilities import digest


async def compile_dependencies(network,publisher,operation,parameters,*,token,user_id):
    pulse=next((row for row in publisher.get('meta',{}).get('supported_pulses',[])
        if (row.get('pulse_name') or row.get('name'))==operation),None)
    compiler=(pulse or {}).get('extensions',{}).get('prompits:call_dependency_compiler')
    if not compiler:return []
    if (not isinstance(compiler,dict) or set(compiler)!={'pulse','schema'} or compiler['schema']!='prompits:declared-call-dependencies/1'
            or not isinstance(user_id,str) or not user_id):raise PermissionError('Current account and exact publisher compiler required.')
    if compiler['pulse']==operation:raise PermissionError('Dependency compilers cannot compile themselves recursively.')
    result=await network.pulse(publisher,compiler['pulse'],{'pulse_name':operation,'params':deepcopy(parameters)},token=token,compile_onward=False)
    if (not isinstance(result,dict) or result.get('schema')!=compiler['schema'] or result.get('publisherAgentId')!=publisher['id']
            or result.get('originalAgentId')!=network.registration.agent_id or result.get('originalUserId')!=user_id
            or result.get('operation')!=operation or result.get('inputDigest')!=digest(parameters)
            or result.get('grantsIssued') is not False or result.get('execution')!='not-started'):
        raise PermissionError('Owner declaration differs from this exact original-user operation.')
    dependencies=result.get('dependencies')
    if not isinstance(dependencies,list) or len(dependencies)>32:raise PermissionError('Bounded exact dependency list required.')
    directory={entry['id']:entry for entry in await network.directory(token)};seen=set()
    for dependency in dependencies:
        if (not isinstance(dependency,dict) or set(dependency)!={'agentId','operation'}
                or any(not isinstance(value,str) or not value for value in dependency.values())):raise PermissionError('Exact peer and operation required.')
        pair=(dependency['agentId'],dependency['operation']);entry=directory.get(pair[0])
        if pair in seen or not entry:raise PermissionError('Dependency missing or repeated.')
        seen.add(pair)
        if entry.get('availability') in ('offline','unavailable'):raise PermissionError('Declared dependency is unavailable.')
        names={row.get('pulse_name') or row.get('name') for row in entry.get('meta',{}).get('supported_pulses',[])}
        if pair[1] not in names:raise PermissionError('Dependency does not publish the selected operation.')
        # Plaza independently checks same-current-owner/original-account at
        # issuance. This compiler neither infers ownership nor bypasses it.
    return deepcopy(dependencies)
