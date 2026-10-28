"""Runtime tool evidence, not remembered claims about installed integrations."""
import json


def diagnostic_summary(payload):
    """Only recognize the structured diagnostic from an executed Git tool."""
    try:
        data = json.loads(str(payload).split('\n', 1)[1])
    except (ValueError, IndexError, TypeError):
        return None
    if not isinstance(data, dict) or data.get('git_tool_enabled') is not True:
        return None
    result = 'My Git/GitHub CLI integration is enabled. '
    if data.get('local_repository'):
        result += 'Current branch: ' + str(data.get('current_branch') or 'detached HEAD') + '. '
        if data.get('remote_url'):
            result += 'Remote: ' + str(data['remote_url']) + '. '
    elif data.get('local_repository') is False:
        result += 'The selected folder is not a Git repository. '
    else:
        result += 'Local repository configuration could not be verified. '
    result += ('Remote read access was verified. ' if data.get('remote_read_access_verified')
               else 'Remote read access was not verified. ')
    if data.get('failure'):
        result += str(data['failure']) + ' '
    return result + ('Credential identity and push permission are not verified. '
                     'GitHub API and browser login were not checked.')


def capability_policy(declarations):
    names = {d.get('name') if isinstance(d, dict) else getattr(d, 'name', '')
             for d in declarations or ()}
    available = 'github_git' in names
    return (
        '\n[CURRENT GIT CAPABILITIES — supersedes earlier session inventories]\n'
        + ('github_git is enabled and callable in this session. For GitHub connection/capability questions, '
           'call github_git action=connectivity before answering; never answer from old conversation claims. '
           'Use status, branches, remotes, log or diff for repository questions. Route commit requests through '
           'review/stage/checks/commit; push requests through prepare_push and fresh explicit push confirmation. '
           'Never invent review IDs or approvals. '
           if available else
           'github_git is not offered to this engine/session. Do not claim it is uninstalled or that '
           'all GitHub integration is absent: it may be disabled or omitted by the model tool budget. ')
        + 'Local Git configuration, remote read access, credential authentication, GitHub API connections, '
          'and browser login are distinct. A successful public ls-remote proves read access, NOT authenticated '
          'identity or push permission. Browser login proves neither Git CLI nor API authentication. '
          'Report each actual tool result and its specific failure; a failed connection does not remove '
          'an installed capability. Tool results override unsupported model assertions about integration.\n'
    )
