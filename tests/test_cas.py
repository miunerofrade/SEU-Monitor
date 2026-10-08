import base64
import threading
from unittest.mock import Mock
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from seu_monitor.core.cas import CASLogin, AuthenticationRequired, fingerprint

CALLBACK = (
    "https://vpn.seu.edu.cn/passport/v1/auth/cas?sfDomain=CAS-auth&ticket=single-use"
)
ENTRY = "https://vpn.seu.edu.cn/auth"
AUTH = "https://auth.seu.edu.cn/dist/?service=https%3A%2F%2Fvpn.seu.edu.cn%2Fpassport%2Fv1%2Fauth%2Fcas"


def response(status=200, data=None, location=None):
    value = Mock(status_code=status, headers={"location": location} if location else {})
    value.json.return_value = data or {}
    return value


def test_follows_redirect_without_consuming_ticket(tmp_path):
    session = Mock()
    session.get.return_value = response(302, location=CALLBACK)
    client = CASLogin(session, "u", "p", tmp_path, threading.Event())
    assert client.follow(ENTRY) == (CALLBACK, None)
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["allow_redirects"] is False


def test_rejects_redirect_to_external_host(tmp_path):
    session = Mock()
    session.get.return_value = response(302, location="https://evil.example/auth")
    with pytest.raises(AuthenticationRequired):
        CASLogin(session, "u", "p", tmp_path, threading.Event()).follow(ENTRY)
    assert session.get.call_count == 1


def test_http_login_rsa_and_sms_without_browser(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = base64.urlsafe_b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode()
    session = Mock()
    session.get.side_effect = [
        response(302, location=AUTH),
        response(),
        response(data={"code": 200}),
        response(302, location=CALLBACK),
    ]
    payloads = []

    def post(url, **kwargs):
        payloads.append((url, kwargs["json"]))
        if url.endswith("/verifyTgt"):
            return response(data={"success": False})
        if url.endswith("/getChiperKey"):
            return response(data={"publicKey": public})
        if url.endswith("/sendStage2Code"):
            return response(data={"code": 200})
        if url.endswith("/casLogin"):
            data = kwargs["json"]
            assert (
                key.decrypt(base64.b64decode(data["password"]), PKCS1v15())
                == b"password"
            )
            if data["mobileVerifyCode"]:
                assert (
                    key.decrypt(base64.b64decode(data["mobileVerifyCode"]), PKCS1v15())
                    == b"123456"
                )
                return response(data={"code": 200, "redirectUrl": "encoded-value"})
            return response(data={"code": 502})
        raise AssertionError(url)

    session.post.side_effect = post
    monkeypatch.setattr("seu_monitor.core.cas.getpass.getpass", lambda *a: "123456")
    assert (
        CASLogin(
            session, "user", "password", tmp_path, threading.Event(), interactive=True
        ).authenticate(ENTRY)
        == CALLBACK
    )
    assert any(url.endswith("/sendStage2Code") for url, data in payloads)
    assert all("single-use" not in str(call) for call in session.get.call_args_list)
    assert (tmp_path / "device.json").stat().st_mode & 0o777 == 0o600


def test_sms_in_service_requires_interactive_command_without_sending(tmp_path):
    client = CASLogin(Mock(), "u", "p", tmp_path, threading.Event())
    client.follow = Mock(return_value=(AUTH, response()))
    client.session.get.return_value = response(data={"code": 200})
    client.encrypt = lambda value: "encrypted"
    client.post = Mock(side_effect=[{"success": False}, {"code": 502}])
    with pytest.raises(AuthenticationRequired, match="monitor vpn --interactive"):
        client.authenticate(ENTRY)
    assert client.post.call_count == 2


def test_device_identity_stays_stable(tmp_path):
    first = fingerprint(tmp_path)
    assert fingerprint(tmp_path) == first
    assert len(first) == 32
