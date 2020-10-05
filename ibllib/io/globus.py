from pathlib import Path

import globus_sdk as globus
from ibllib.io import params


def as_globus_path(path):
    """
    Convert a path into one suitable for the Globus TransferClient.

    :param path: A path str or Path instance
    :return: A formatted path string

    Examples:
        # A Windows path
        >>> as_globus_path('E:\\FlatIron\\integration')
        >>> '/~/E/FlatIron/integration'

        # A relative POSIX path
        >>> as_globus_path('../data/integration')
        >>> '/~/mnt/data/integration'

        # A globus path
        >>> as_globus_path('/~/E/FlatIron/integration')
        >>> '/~/E/FlatIron/integration'
    """
    if str(path).startswith('/~/'):
        return path
    path = Path(path).resolve()
    if path.drive:
        path = path.as_posix().replace(':', '', 1)
    return '/~/' + str(path)


def _login(globus_client_id, refresh_tokens=False):

    client = globus.NativeAppAuthClient(globus_client_id)
    client.oauth2_start_flow(refresh_tokens=refresh_tokens)

    authorize_url = client.oauth2_get_authorize_url()
    print('Please go to this URL and login: {0}'.format(authorize_url))
    auth_code = input(
        'Please enter the code you get after login here: ').strip()

    token_response = client.oauth2_exchange_code_for_tokens(auth_code)
    globus_transfer_data = token_response.by_resource_server['transfer.api.globus.org']

    token = dict(refresh_token=globus_transfer_data['refresh_token'],
                 transfer_token=globus_transfer_data['access_token'],
                 expires_at_s=globus_transfer_data['expires_at_seconds'],
                 )
    return token


def login(globus_client_id):
    token = _login(globus_client_id, refresh_tokens=False)
    authorizer = globus.AccessTokenAuthorizer(token['transfer_token'])
    tc = globus.TransferClient(authorizer=authorizer)
    return tc


def setup(globus_client_id, str_app='globus'):
    # Lookup and manage consents there
    # https://auth.globus.org/v2/web/consents
    gtok = _login(globus_client_id, refresh_tokens=True)
    params.write(str_app, gtok)


def login_auto(globus_client_id, str_app='globus'):
    token = params.read(str_app)
    required_fields = {'refresh_token', 'transfer_token', 'expires_at_s'}
    if not (token and required_fields.issubset(token.as_dict())):
        raise ValueError("Token file doesn't exist, run ibllib.io.globus.setup first")
    client = globus.NativeAppAuthClient(globus_client_id)
    client.oauth2_start_flow(refresh_tokens=True)
    authorizer = globus.RefreshTokenAuthorizer(token.refresh_token, client)
    return globus.TransferClient(authorizer=authorizer)


# Login functions coming from alyx
# ------------------------------------------------------------------------------------------------

def globus_client_id():
    return params.read('one_params').GLOBUS_CLIENT_ID


def get_config_path(path=''):
    path = op.expanduser(op.join('~/.ibllib', path))
    os.makedirs(op.dirname(path), exist_ok=True)
    return path


def create_globus_client():
    client = globus.NativeAppAuthClient(globus_client_id())
    client.oauth2_start_flow(refresh_tokens=True)
    return client


def create_globus_token():
    client = create_globus_client()
    print('Please go to this URL and login: {0}'
          .format(client.oauth2_get_authorize_url()))
    get_input = getattr(__builtins__, 'raw_input', input)
    auth_code = get_input('Please enter the code here: ').strip()
    token_response = client.oauth2_exchange_code_for_tokens(auth_code)
    globus_transfer_data = token_response.by_resource_server['transfer.api.globus.org']

    data = dict(transfer_rt=globus_transfer_data['refresh_token'],
                transfer_at=globus_transfer_data['access_token'],
                expires_at_s=globus_transfer_data['expires_at_seconds'],
                )
    path = get_config_path('globus-token.json')
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True)


def get_globus_transfer_rt():
    path = get_config_path('globus-token.json')
    if not op.exists(path):
        return
    with open(path, 'r') as f:
        return json.load(f).get('transfer_rt', None)


def globus_transfer_client():
    transfer_rt = get_globus_transfer_rt()
    if not transfer_rt:
        create_globus_token()
        transfer_rt = get_globus_transfer_rt()
    client = create_globus_client()
    authorizer = globus.RefreshTokenAuthorizer(transfer_rt, client)
    tc = globus.TransferClient(authorizer=authorizer)
    return tc


# Globus wrapper
# ------------------------------------------------------------------------------------------------

def local_endpoint():
    path = Path.home().joinpath(".globusonline/lta/client-id.txt")
    if path.exists():
        return path.read_text()


ENDPOINTS = {
    'test': ('2bfac104-12b1-11ea-bea5-02fcc9cdd752', '/~/mnt/xvdf/Data/'),
    'flatiron': ('15f76c0c-10ee-11e8-a7ed-0a448319c2f8', '/~/'),
    'local': (local_endpoint(), '/~/ssd/ephys/globus/'),
}


class Globus:
    def __init__(self):
        self._tc = globus_transfer_client()

    def ls(self, endpoint, path=''):
        endpoint, root = ENDPOINTS.get(endpoint, endpoint)
        if not root.endswith('/'):
            root += '/'
        if path.startswith('/'):
            path = path[1:]
        path = root + path
        assert '//' not in path
        out = []
        for entry in self._tc.operation_ls(endpoint, path=path):
            out.append((entry['name'], entry['size'] if entry['type'] == 'file' else None))
        return out
