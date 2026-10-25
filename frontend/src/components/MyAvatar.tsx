import { useMe } from '../api/hooks'
import { ownPictureUrl } from '../lib/avatars'
import { Avatar } from './bits'

/**
 * You, as a picture: the sending address's own picture, else your account picture, else your
 * initials. `onlyPicture` draws nothing without a picture (the assistant chat).
 */
export function MyAvatar({ email, size = 'xs', onlyPicture = false }: { email?: string | null; size?: 'md' | 'sm' | 'xs'; onlyPicture?: boolean }) {
  const me = useMe().data
  const src = ownPictureUrl(me, email)
  if (!me || (onlyPicture && !src)) return null
  return <Avatar who={{ email: email ?? me.user.email, name: me.user.name }} size={size} src={src} />
}
