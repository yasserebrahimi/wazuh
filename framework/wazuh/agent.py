# Copyright (C) 2015-2019, Wazuh Inc.
# Created by Wazuh, Inc. <info@wazuh.com>.
# This program is free software; you can redistribute it and/or modify it under the terms of GPLv2

import hashlib
import operator
from glob import glob
from os import chmod, path, listdir
from shutil import copyfile

from wazuh import common, configuration
from wazuh.InputValidator import InputValidator
from wazuh.core.core_agent import WazuhDBQueryAgents, WazuhDBQueryDistinctAgents, WazuhDBQueryGroupByAgents
from wazuh.database import Connection
from wazuh.exception import WazuhError, WazuhInternalError, WazuhException, create_exception_dic
from wazuh.rbac.decorators import expose_resources
from wazuh.utils import chmod_r, chown_r, get_hash, mkdir_with_mode, md5, process_array
from wazuh.core.core_agent import Agent


@expose_resources(actions=["agent:read"], resources=["agent:id:*"], post_proc_func=None)
def get_distinct_agents(agent_list=None, offset=0, limit=common.database_limit, sort=None, search=None, select=None, 
                        fields=None, q=''):
    """ Gets all the different combinations that agents have for the selected fields. It also indicates the total
    number of agents that have each combination.

    :param agent_list: List of agents ID's.
    :param offset: First item to return.
    :param limit: Maximum number of items to return.
    :param sort: Sorts the items. Format: {"fields":["field1","field2"],"order":"asc|desc"}.
    :param select: Select fields to return. Format: {"fields":["field1","field2"]}.
    :param search: Looks for items with the specified string. Format: {"fields": ["field1","field2"]}
    :param q: Defines query to filter in DB.
    :param fields: Fields to group by
    :return: Dict: {data:{'items': List of items, 'totalItems': Number of items (without applying the limit)}}
    """
    db_query = WazuhDBQueryGroupByAgents(filter_fields=fields, offset=offset, limit=limit, sort=sort, search=search, 
                                         select=select, query=q, filters={'id': agent_list}, min_select_fields=set(), 
                                         count=True, get_data=True)
    data = db_query.run()
    
    return data


@expose_resources(actions=["agent:read"], resources=["agent:id:*"], post_proc_func=None)
def get_agents_summary_status(agent_list=None):
    """Counts the number of agents by status.

    :param agent_list: List of agents ID's.
    :return: Dictionary with keys: total, active, disconnected, never_connected, pending and values: count
    """
    db_query = WazuhDBQueryAgents(limit=None, select=['status'], filters={'id': agent_list})
    data = db_query.run()

    result = {'active': 0, 'disconnected': 0, 'never_connected': 0, 'pending': 0, 'total': 0}
    for agent in data['items']:
        result[agent['status']] += 1
        result['total'] += 1

    return result


@expose_resources(actions=["agent:read"], resources=["agent:id:*"], post_proc_func=None)
def get_agents_summary_os(agent_list=None, offset=None, limit=None, search=None, q=None):
    """Gets a list of available OS.

    :param agent_list: List of agents ID's.
    :param offset: First item to return.
    :param limit: Maximum number of items to return.
    :param search: Looks for items with the specified string.
    :param q: Query to filter results.
    :return: Dictionary: {'items': array of items, 'totalItems': Number of items (without applying the limit)}
    """
    db_query = WazuhDBQueryDistinctAgents(offset=offset, limit=limit, search=search, select=['os.platform'],
                                          filters={'id': agent_list}, default_sort_field='os_platform', query=q,
                                          min_select_fields=set())
    data = db_query.run()

    return data


@expose_resources(actions=["agent:restart"], resources=["agent:id:{agent_list}"])
def restart_agents(agent_list=None):
    """Restarts a list of agents

    :param agent_list: List of agents ID's.
    :return: Message.
    """
    affected_agents = list()
    failed_ids = list()
    for agent_id in agent_list:
        try:
            Agent(agent_id).restart()
            affected_agents.append(agent_id)
        except WazuhException as e:
            failed_ids.append(create_exception_dic(agent_id, e))

    return {'affected_items': affected_agents,
            'failed_items': failed_ids,
            'str_priority': ['Restart command sent to all agents',
                             'Could not send command to some agents',
                             'Could not send command to any agent']}


@expose_resources(actions=["agent:read"], resources=["agent:id:{agent_list}"], post_proc_func=None)
def get_agents(agent_list=None, offset=0, limit=common.database_limit, sort=None, search=None, select=None, 
               filters=None, q=''):
    """Gets a list of available agents with basic attributes.

    :param agent_list: List of agents ID's.
    :param offset: First item to return.
    :param limit: Maximum number of items to return.
    :param sort: Sorts the items. Format: {"fields":["field1","field2"],"order":"asc|desc"}.
    :param select: Select fields to return. Format: {"fields":["field1","field2"]}.
    :param search: Looks for items with the specified string. Format: {"fields": ["field1","field2"]}
    :param filters: Defines required field filters. Format: {"field1":"value1", "field2":["value2","value3"]}
    :param q: Defines query to filter in DB.
    :return: Dictionary: {'items': array of items, 'totalItems': Number of items (without applying the limit)}
    """
    if filters is None:
        filters = dict()
    filters['id'] = agent_list
    db_query = WazuhDBQueryAgents(offset=offset, limit=limit, sort=sort, search=search, select=select, filters=filters, 
                                  query=q)
    data = db_query.run()

    return data


@expose_resources(actions=["agent:read"], resources=["agent:id:{agent_list}"], post_proc_func=None)
def get_agents_keys(agent_list=None):
    """Get the key of existing agents

    :param agent_list: List of agents ID's.
    :return: Agent key.
    """
    items = list()
    for agent_id in agent_list:
        try:
            items.append({'id': agent_id, 'key': Agent(agent_id).get_key()})
        except WazuhException:
            pass

    return {'items': items, 'totalItems': len(items)}


@expose_resources(actions=["agent:delete"], resources=["agent:id:{agent_list}"],
                  post_proc_kwargs={'extra_fields': ['older_than'], 'exclude_codes': [1703]})
def delete_agents(agent_list=None, backup=False, purge=False, status="all", older_than="7d"):
    """Deletes a list of agents.

    :param agent_list: List of agents ID's.
    :param backup: Create backup before removing the agent.
    :param purge: Delete definitely from key store.
    :param older_than:  Filters out disconnected agents for longer than specified. Time in seconds | "[n_days]d" |
    "[n_hours]h" | "[n_minutes]m" | "[n_seconds]s". For never_connected agents, uses the register date.
    :param status: Filters by agent status: active, disconnected or never_connected. Multiples statuses separated
    by commas.
    :return: Dictionary with affected_agents (deleted agents), timeframe applied, failed_ids if it necessary
    (agents that could not be deleted), and a message.
    """
    db_query = WazuhDBQueryAgents(limit=None, select=["id"], filters={'older_than': older_than, 'status': status,
                                                                      'id': agent_list})
    data = db_query.run()
    id_purgeable_agents = list(map(operator.itemgetter('id'), data['items']))

    failed_ids = list()
    affected_agents = list()
    for agent_id in agent_list:
        try:
            if agent_id == "000":
                raise WazuhError(1703)
            else:
                my_agent = Agent(agent_id)
                my_agent.load_info_from_db()
                if agent_id not in id_purgeable_agents:
                    raise WazuhError(1731, extra_message="The agent has a status different to '{0}' or the specified "
                                                         "time frame 'older_than {1}' does not apply."
                                                         .format(status, older_than))
                my_agent.remove(backup, purge)
                affected_agents.append(agent_id)
        except WazuhException as e:
            failed_ids.append(create_exception_dic(agent_id, e))

    result = {'affected_items': affected_agents,
              'failed_items': failed_ids,
              'str_priority': ['All selected agents were deleted',
                               'Some agents were not deleted',
                               'No agents were deleted']}

    return result


@expose_resources(actions=["agent:create"], resources=["*:*:*"], post_proc_func=None)
def add_agent(name=None, agent_id=None, key=None, ip='any', force_time=-1):
    """Adds a new Wazuh agent.

    :param name: name of the new agent.
    :param agent_id: id of the new agent.
    :param ip: IP of the new agent. It can be an IP, IP/NET or ANY.
    :param key: name of the new agent.
    :param force_time: Remove old agent with same IP if disconnected since <force_time> seconds.
    :return: Agent ID.
    """
    # Check length of agent name
    if len(name) > 128:
        raise WazuhError(1738)

    new_agent = Agent(name=name, ip=ip, id=agent_id, key=key, force=force_time)

    return {'id': new_agent.id, 'key': new_agent.key}


@expose_resources(actions=["group:read"], resources=["group:id:*"], post_proc_func=None)
def get_groups(group_list=None, offset=0, limit=common.database_limit, sort_by=None, sort_ascending=True,
                   search_text=None, complementary_search=False, search_in_fields=None, hash_algorithm='md5'):
    """Gets the existing groups.

    :param group_list: List of Group names.
    :param offset: First item to return.
    :param limit: Maximum number of items to return.
    :param sort_by: Fields to sort the items by
    :param sort_ascending: Sort in ascending (true) or descending (false) order
    :param search_text: Text to search
    :param complementary_search: Find items without the text to search
    :param search_in_fields: Fields to search in
    :param hash_algorithm: hash algorithm used to get mergedsum and configsum.
    :return: Dictionary: {'items': array of items, 'totalItems': Number of items (without applying the limit)}
    """
    try:
        # Connect DB
        db_global = glob(common.database_path_global)
        if not db_global:
            raise WazuhInternalError(1600)

        conn = Connection(db_global[0])

        # Group names
        data = []
        for group in group_list:
            full_entry = path.join(common.shared_path, group)

            # Get the id of the group
            query = "SELECT id FROM `group` WHERE name = :group_id"
            request = {'group_id': group}
            conn.execute(query, request)
            id_group = conn.fetch()

            if id_group is None:
                continue

            # Group count
            query = "SELECT {0} FROM belongs WHERE id_group = :id"
            request = {'id': id_group}
            conn.execute(query.format('COUNT(*)'), request)

            # merged.mg and agent.conf sum
            merged_sum = get_hash(path.join(full_entry, "merged.mg"), hash_algorithm)
            conf_sum = get_hash(path.join(full_entry, "agent.conf"), hash_algorithm)

            item = {'count': conn.fetch(), 'name': group}

            if merged_sum:
                item['mergedSum'] = merged_sum

            if conf_sum:
                item['configSum'] = conf_sum

            data.append(item)
    except WazuhError as e:
        raise e
    except Exception as e:
        raise WazuhInternalError(1736, extra_message=str(e))

    return process_array(data, search_text=search_text, search_in_fields=search_in_fields,
                         complementary_search=complementary_search, sort_by=sort_by, sort_ascending=sort_ascending,
                         offset=offset, limit=limit)


@expose_resources(actions=["group:read"], resources=["group:id:{group_list}"], post_proc_func=None)
def get_group_files(group_list=None, offset=0, limit=common.database_limit, search_text=None, search_in_fields=None,
                    complementary_search=False, sort_by=None, sort_ascending=True, hash_algorithm='md5'):
    """Gets the group files.

    :param group_list: List of Group names.
    :param offset: First item to return.
    :param limit: Maximum number of items to return.
    :param sort_by: Fields to sort the items by
    :param sort_ascending: Sort in ascending (true) or descending (false) order
    :param search_text: Text to search
    :param complementary_search: Find items without the text to search
    :param search_in_fields: Fields to search in
    :param hash_algorithm: hash algorithm used to get mergedsum and configsum.
    :return: Dictionary: {'items': array of items, 'totalItems': Number of items (without applying the limit)}
    """
    # We access unique group_id from list, this may change if and when we decide to add option to get files for
    # a list of groups
    group_id = group_list[0]
    group_path = common.shared_path
    if group_id:
        if not Agent.group_exists(group_id):
            raise WazuhError(1710, extra_message=group_id)
        group_path = path.join(common.shared_path, group_id)

    if not path.exists(group_path):
        raise WazuhError(1006, extra_message=group_path)

    try:
        data = []
        for entry in listdir(group_path):
            item = dict()
            try:
                item['filename'] = entry
                item['hash'] = get_hash(path.join(group_path, entry), hash_algorithm)
                data.append(item)
            except (OSError, IOError):
                pass

        try:
            # ar.conf
            ar_path = path.join(common.shared_path, 'ar.conf')
            data.append({'filename': "ar.conf", 'hash': get_hash(ar_path, hash_algorithm)})
        except (OSError, IOError):
            pass

        return process_array(data, search_text=search_text, search_in_fields=search_in_fields,
                             complementary_search=complementary_search, sort_by=sort_by, sort_ascending=sort_ascending,
                             offset=offset, limit=limit)
    except WazuhError as e:
        raise e
    except Exception as e:
        raise WazuhInternalError(1727, extra_message=str(e))


@expose_resources(actions=["group:create"], resources=["*:*:*"], post_proc_func=None)
def create_group(group_id):
    """Creates a group.

    :param group_id: Group ID.
    :return: Confirmation message.
    """
    # Input Validation of group_id
    if not InputValidator().group(group_id):
        raise WazuhError(1722)

    group_path = path.join(common.shared_path, group_id)

    if group_id.lower() == "default" or path.exists(group_path):
        raise WazuhError(1711, extra_message=group_id)

    # Create group in /etc/shared
    group_def_path = path.join(common.shared_path, 'agent-template.conf')
    try:
        mkdir_with_mode(group_path)
        copyfile(group_def_path, path.join(group_path, 'agent.conf'))
        chown_r(group_path, common.ossec_uid(), common.ossec_gid())
        chmod_r(group_path, 0o660)
        chmod(group_path, 0o770)
        msg = "Group '{0}' created.".format(group_id)
    except Exception as e:
        raise WazuhInternalError(1005, extra_message=str(e))

    return msg


@expose_resources(actions=["group:delete"], resources=["group:id:{group_list}"],
                  post_proc_kwargs={'extra_affected': 'affected_agents'})
def delete_groups(group_list=None):
    """Delete a list of groups and remove it from every agent assignments.

    :param group_list: List of Group names.
    :return: Confirmation message.
    """
    failed_groups = list()
    affected_groups = list()
    affected_agents = set()
    for group_id in group_list:
        try:
            removed = Agent.delete_single_group(group_id)
            affected_groups.append(group_id)
            affected_agents.update(removed['affected_items'])
            Agent.remove_multi_group(set(group_id.lower()))
        except WazuhException as e:
            failed_groups.append(create_exception_dic(group_id, e))

    result = {'affected_items': affected_groups,
              'failed_items': failed_groups,
              'affected_agents': sorted(affected_agents, key=int),
              'str_priority': ['All selected groups were deleted',
                               'Some groups were not deleted',
                               'No groups were deleted']}

    return result


@expose_resources(actions=["agent:modify_group"], resources=["agent:id:{agent_list}"])
def assign_agents_to_group(group_id=None, agent_list=None, replace=False):
    """Assign a list of agents to a group

    :param group_id: Group ID.
    :param agent_list: List of Agent IDs.
    :param replace: Whether to append new group to current agent's group or replace it.
    :return: Confirmation message.
    """
    failed_ids = list()
    affected_agents = list()

    # Check if the group exists
    if not Agent.group_exists(group_id):
        raise WazuhError(1710)

    for agent_id in agent_list:
        try:
            Agent.add_group_to_agent(agent_id=agent_id, group_id=group_id, replace=replace)
            affected_agents.append(agent_id)
        except WazuhException as e:
            failed_ids.append(create_exception_dic(agent_id, e))

    result = {'affected_items': affected_agents,
              'failed_items': failed_ids,
              'str_priority': ['All selected agents were assigned to {0}{1}'.format
                               (group_id, ' and removed from the other groups' if replace else ''),
                               'Some agents were not assigned to {0}{1}'.format
                               (group_id, ' and removed from the other groups' if replace else ''),
                               'No agents assigned to {0}'.format(group_id)]}

    return result


@expose_resources(actions=["group:modify_assignments"], resources=['group:id:{group_id}'], post_proc_func=None,
                  stack=True)
@expose_resources(actions=["agent:modify_group"], resources=['agent:id:{agent_list}'],
                  post_proc_kwargs={'exclude_codes': [1734]})
def remove_agents_from_group(group_id=None, agent_list=None):
    """Removes agents assignment from a specified group

    :param group_id: Group ID.
    :param agent_list: List of Agent IDs.
    :return: Confirmation message.
    """
    failed_ids = list()
    affected_agents = list()

    # Check if the group exists
    if not Agent.group_exists(group_id[0]):
        raise WazuhError(1710)

    db_query = WazuhDBQueryAgents(limit=None, filters={'id': agent_list}, query=f'group={group_id[0]}')
    agents_in_group = set(map(operator.itemgetter('id'), db_query.run()['items']))

    for agent_id in agent_list:
        try:
            if agent_id in set(agent_list) - agents_in_group:
                if agent_id == "000":
                    raise WazuhError(1703)
                else:
                    raise WazuhError(1734)
            Agent.unset_single_group_agent(agent_id=agent_id, group_id=group_id[0], force=False)
            affected_agents.append(agent_id)
        except WazuhException as e:
            failed_ids.append(create_exception_dic(agent_id, e))

    result = {'affected_items': affected_agents,
              'failed_items': failed_ids,
              'str_priority': ['All selected agents were removed from {}'.format(group_id[0]),
                               'Some agents were not removed from {}'.format(group_id[0]),
                               'No agents removed from {}'.format(group_id[0])]}

    return result


@expose_resources(actions=["agent:modify_group"], resources=["agent:id:{agent_list}"])
def remove_agents_from_all_groups(agent_list=None, force=False):
    """Removes a list of agents assigment from all groups

    :param agent_list: List of agents ID's.
    :param force: Do not check if agent exists
    :return: Confirmation message.
    """
    affected_agents = list()
    failed_ids = list()
    for agent_id in agent_list:
        try:
            if agent_id == '000':
                raise WazuhError(1703)
            else:
                if not force:
                    Agent(agent_id).get_basic_information()  # Check if agent exists
                Agent.set_agent_group_file(agent_id, 'default')  # Reset agent assignment to 'default' group
                affected_agents.append(agent_id)
        except WazuhException as e:
            failed_ids.append(create_exception_dic(agent_id, e))

    result = {'affected_items': affected_agents,
              'failed_items': failed_ids,
              'str_priority': ['All selected agents were removed from all groups. Group reverted to default.',
                               'Some agents were not removed from from all groups. Affected agents group reverted to '
                               'default',
                               'No agents removed from from all groups']}

    return result


@expose_resources(actions=["agent:read"], resources=["agent:id:*"], post_proc_func=None)
def get_outdated_agents(agent_list=None, offset=None, limit=None, sort=None, search=None, select=None, q=None):
    """Gets the outdated agents.

    :param agent_list: List of agents ID's.
    :param offset: First item to return.
    :param limit: Maximum number of items to return.
    :param sort: Sorts the items. Format: {"fields":["field1","field2"],"order":"asc|desc"}.
    :param search: Looks for items with the specified string.
    :param select: Select fields to return. Format: {"fields":["field1","field2"]}.
    :param q: Defines query to filter in DB.
    :return: Dictionary: {'items': array of items, 'totalItems': Number of items (without applying the limit)}
    """
    filters = dict()
    filters['id'] = agent_list

    # Get manager version
    manager = Agent(id='000')
    manager.load_info_from_db()

    db_query = WazuhDBQueryAgents(offset=offset, limit=limit, sort=sort, search=search, select=select,
                                  query=f"version!={manager.version}" + (';' + q if q else ''), filters=filters)
    data = db_query.run()

    return data


@expose_resources(actions=["agent:upgrade"], resources=["agent:id:{agent_list}"], post_proc_func=None)
def upgrade_agents(agent_list=None, wpk_repo=None, version=None, force=False, chunk_size=None, use_http=False):
    """Read upgrade result output from agent.

    :param agent_list: List of agents ID's.
    :param wpk_repo: URL for WPK download
    :param version: Version to upgrade to
    :param force: force the update even if it is a downgrade
    :param chunk_size: size of each update chunk
    :param use_http: False for HTTPS protocol, True for HTTP protocol
    :return: Upgrade message.
    """
    # We access unique agent_id from list, this may change if and when we decide to add option to upgrade a list of
    # agents
    agent_id = agent_list[0]

    return Agent(agent_id).upgrade(wpk_repo=wpk_repo, version=version, force=True if int(force) == 1 else False,
                                   chunk_size=chunk_size, use_http=use_http)


@expose_resources(actions=["agent:upgrade"], resources=["agent:id:{agent_list}"], post_proc_func=None)
def get_upgrade_result(agent_list=None, timeout=3):
    """Read upgrade result output from agent.

    :param agent_list: List of agents ID's.
    :param timeout: Maximum time for the call to be considered failed
    :return: Upgrade result.
    """
    # We access unique agent_id from list, this may change if and when we decide to add option to upgrade a list of
    # agents
    agent_id = agent_list[0]

    return Agent(agent_id).upgrade_result(timeout=int(timeout))


@expose_resources(actions=["agent:upgrade"], resources=["agent:id:{agent_list}"], post_proc_func=None)
def upgrade_agents_custom(agent_list=None, file_path=None, installer=None):
    """Read upgrade result output from agent.

    :param agent_list: List of agents ID's.
    :param file_path: Path to the installation file
    :param installer: Selected installer
    :return: Upgrade message.
    """
    if not file_path or not installer:
        raise WazuhInternalError(1307)

    # We access unique agent_id from list, this may change if and when we decide to add option to upgrade a list of
    # agents
    agent_id = agent_list[0]

    return Agent(agent_id).upgrade_custom(file_path=file_path, installer=installer)


@expose_resources(actions=["agent:read"], resources=["agent:id:{agent_list}"], post_proc_func=None)
def get_agents_config(agent_list=None, component=None, config=None):
    """Read selected configuration from agent.

    :param agent_list: List of agents ID's.
    :param component: Selected component
    :param config: Configuration to get, written on disk
    :return: Loaded configuration in JSON.
    """
    # We access unique agent_id from list, this may change if and when we decide a final way to handle get responses
    # with failed ids and a list of agents
    agent_id = agent_list[0]
    my_agent = Agent(agent_id)
    my_agent.load_info_from_db()

    if my_agent.status != "active":
        raise WazuhError(1740)

    return my_agent.getconfig(component=component, config=config)


@expose_resources(actions=["agent:read"], resources=["agent:id:{agent_list}"], post_proc_func=None)
def get_agents_sync_group(agent_list=None):
    """Get agents configuration sync status.

    :param agent_list: List of agents ID's.
    :return Sync status
    """
    # We access unique agent_id from list, this may change when we decide a final way to handle get responses with
    # failed ids
    agent_id = agent_list[0]
    if agent_id == "000":
        raise WazuhError(1703)
    else:
        try:
            # Check if agent exists and it is active
            agent_info = Agent(agent_id).get_basic_information()
            # Check if it has a multigroup
            if len(agent_info['group']) > 1:
                multi_group = ','.join(agent_info['group'])
                multi_group = hashlib.sha256(multi_group.encode()).hexdigest()[:8]
                agent_group_merged_path = path.join(common.multi_groups_path, multi_group, "merged.mg")
            else:
                agent_group_merged_path = path.join(common.shared_path, agent_info['group'][0], "merged.mg")
            return {'synced': md5(agent_group_merged_path) == agent_info['mergedSum']}
        except (IOError, KeyError):
            # The file couldn't be opened and therefore the group has not been synced
            return {'synced': False}
        except WazuhError as e:
            raise e
        except Exception as e:
            raise WazuhInternalError(1739, extra_message=str(e))


@expose_resources(actions=["group:read"], resources=["group:id:{group_list}"], post_proc_func=None)
def get_file_conf(group_list=None, type_conf=None, return_format=None, filename=None):
    """ Reads configuration file for specified group

    :param group_list: List of Group names.
    :param type_conf: Type of file
    :param return_format: Format of the answer (xml or json)
    :param filename: Filename to read config from.
    :return: agent.conf as dictionary.
    """
    # We access unique group_id from list, this may change if and when we decide to add option to get configuration
    # files for a list of groups
    group_id = group_list[0]

    return configuration.get_file_conf(filename, group_id=group_id, type_conf=type_conf, return_format=return_format)


@expose_resources(actions=["group:read"], resources=["group:id:{group_list}"], post_proc_func=None)
def get_agent_conf(group_list=None, offset=0, limit=common.database_limit, filename='agent.conf'):
    """ Reads agent conf for specified group

    :param group_list: List of Group names.
    :param offset: First item to return.
    :param limit: Maximum number of items to return.
    :param filename: Filename to read config from.
    :return: agent.conf as dictionary.
    """
    # We access unique group_id from list, this may change if and when we decide to add option to get agent conf for
    # a list of groups
    group_id = group_list[0]

    return configuration.get_agent_conf(group_id=group_id, offset=offset, limit=limit, filename=filename)


@expose_resources(actions=["group:update_config"], resources=["group:id:{group_list}"], post_proc_func=None)
def upload_group_file(group_list=None, file_data=None, file_name='agent.conf'):
    """Updates a group file

    :param group_list: List of Group names.
    :param file_data: Relative path of temporary file to upload
    :param file_name: File name to update
    :return: Confirmation message in string
    """
    # We access unique group_id from list, this may change if and when we decide to add option to update files for
    # a list of groups
    group_id = group_list[0]

    return configuration.upload_group_file(group_id, file_data, file_name=file_name)
