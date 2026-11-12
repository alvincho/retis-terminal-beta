"""Plaza's neutral label for an account that has not published a name.

Kept on its own so a client can recognise the label without importing Plaza's trust code, which
would pull Plaza's server modules into what the client ships.
"""

# The start of the neutral label for an account that has not published a name; the account's last
# four characters follow it.
UNNAMED = 'Plaza account …'
