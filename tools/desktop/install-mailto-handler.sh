#!/bin/sh
# Make a mailr server this Linux desktop's email app: email (mailto:) links in any program open a
# draft in mailr, in the default browser. Writes ~/.local/share/applications/mailr-compose.desktop
# and makes it the default for x-scheme-handler/mailto (xdg-mime). Nothing is sent: the link only
# fills a draft. User guide: site/docs/user-guide/email-links.md.
#
#   tools/desktop/install-mailto-handler.sh https://mail.example.com
#
# To undo: choose another email app (KDE: System Settings → Default Applications → Email client;
# GNOME: Settings → Apps → Default apps), or delete the .desktop file and ~/.local/bin/mailr-compose.
set -eu

if [ $# -ne 1 ]; then
  echo "usage: $0 <instance-url>   (for example https://mail.example.com)" >&2
  exit 2
fi
url=${1%/}
case "$url" in
  https://*) ;;
  http://localhost*|http://127.0.0.1*) ;;
  http://*) echo "warning: $url is not https; your browser may refuse the sign-in cookie" >&2 ;;
  *) echo "error: the address must start with https:// (got: $1)" >&2; exit 2 ;;
esac
case "$url" in
  *[\"\ \`\$\\]*) echo "error: the address may not contain quotes, spaces, \$, \` or backslashes" >&2; exit 2 ;;
esac

apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
file=$apps/mailr-compose.desktop
bin=$HOME/.local/bin/mailr-compose
mkdir -p "$apps" "$HOME/.local/bin"
# A helper takes the whole mailto: link as its one argument and puts it into the query as it is (not
# encoded): mailr reads a "?mailto=mailto:…" query raw (frontend/src/lib/mailto.ts). Opened from the
# menu, the argument is empty and an empty draft opens. The desktop entry only calls the helper with
# %u, so no quoting rules of the desktop-entry spec are involved (field codes may not sit in quotes).
cat > "$bin" <<HELPER
#!/bin/sh
exec xdg-open "$url/compose?mailto=\${1:-}"
HELPER
chmod 755 "$bin"
cat > "$file" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Mailr
GenericName=Email
Comment=Write an email in Mailr ($url)
Exec=$bin %u
Icon=mail-message-new
Terminal=false
Categories=Network;Email;
MimeType=x-scheme-handler/mailto;
DESKTOP
chmod 644 "$file"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$apps" 2>/dev/null || true
fi
xdg-mime default mailr-compose.desktop x-scheme-handler/mailto
echo "wrote $file"
echo "default email app: $(xdg-mime query default x-scheme-handler/mailto)"
echo "try it: xdg-open 'mailto:someone@example.com?subject=Hello'"
