"""Test doubles shipped with the auth service.

Kept in the package (rather than in the test tree) so that other services' integration
tests and local tooling can drive a real login flow without an SMS provider.
"""

from __future__ import annotations


class RecordingOtpSender:
    """An :class:`~marsool_auth.otp.OtpSender` that records codes instead of sending them.

    Satisfies the ``OtpSender`` protocol structurally.
    """

    channel = "test"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, *, phone: str, code: str, ttl_seconds: int) -> None:
        self.sent.append((phone, code))

    def latest_code_for(self, phone: str) -> str:
        """Return the most recent code delivered to ``phone``.

        Raises:
            AssertionError: If no code was delivered, which is nearly always a test bug
                rather than an expected condition.
        """
        codes = [code for sent_phone, code in self.sent if sent_phone == phone]
        if not codes:
            raise AssertionError(f"no OTP was sent to {phone}")
        return codes[-1]

    def clear(self) -> None:
        self.sent.clear()
