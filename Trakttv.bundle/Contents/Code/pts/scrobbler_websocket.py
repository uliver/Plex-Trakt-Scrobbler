from plex.media_server import PMS
from pts.scrobbler import Scrobbler
from data.watch_session import WatchSession


class WebSocketScrobbler(Scrobbler):
    def create_session(self, session_key, state):
        """
        :type session_key: str
        :type state: str

        :rtype: WatchSession or None
        """

        Log.Debug('Creating a WatchSession for the current media')

        video_section = PMS.get_video_session(session_key)
        if not video_section:
            return None

        player_section = video_section.findall('Player')
        if len(player_section):
            player_section = player_section[0]

        session = WatchSession.from_section(
            video_section, state,
            PMS.metadata(video_section.get('ratingKey')),
            PMS.client(player_section.get('machineIdentifier'))
        )
        session.save()

        return session

    def update_session(self, session, view_offset):
        Log.Debug('Trying to update the current WatchSession (session key: %s)' % session.key)

        video_section = PMS.get_video_session(session.key)
        if not video_section:
            Log.Warn('Session was not found on media server')
            return False

        Log.Debug('last item key: %s, current item key: %s' % (session.item_key, video_section.get('ratingKey')))

        if session.item_key != video_section.get('ratingKey'):
            Log.Info('Invalid Session: Media changed')
            return False

        if not session.metadata:
            Log.Debug('Invalid Session: Missing metadata')
            return False

        if session.metadata.get('duration', 0) <= 0:
            Log.Debug('Invalid Session: Invalid duration')
            return False

        session.last_view_offset = view_offset
        session.update_required = False

        return True

    def get_session(self, session_key, state, view_offset):
        session = WatchSession.load(session_key)

        if session:
            if session.last_view_offset and session.last_view_offset > view_offset:
                Log.Debug('View offset has gone backwards (last: %s, cur: %s)' % (
                    session.last_view_offset, view_offset
                ))

                # First try update the session if the media hasn't changed
                # otherwise delete the session
                if not self.update_session(session, view_offset):
                    Log.Debug('Media changed, deleting the session')
                    session.delete()
                    return None

            if session.skip:
                return None

            if state == 'playing' and session.update_required:
                Log.Debug('Session update required, updating the session...')

                if not self.update_session(session, view_offset):
                    Log.Debug('Media changed, deleting the session')
                    session.delete()
                    return None
        else:
            session = self.create_session(session_key, state)

        return session

    def update(self, session_key, state, view_offset):
        session = self.get_session(session_key, state, view_offset)
        if not session:
            Log.Info('Invalid session, unable to continue')
            return

        # Ignore sessions flagged as 'skip'
        if session.skip:
            return

        # Ensure we are only scrobbling for the myPlex user listed in preferences
        if not self.valid_user(session):
            Log.Info('Ignoring item [%s](%s) played by other user: %s' % (
                session_key,
                session.get_title(),
                session.user.title if session.user else None
            ))
            session.skip = True

        # Ensure we are only scrobbling for the client listed in preferences
        if not self.valid_client(session):
            Log.Info('Ignoring item [%s](%s) played by other client: %s' % (
                session_key,
                session.get_title(),
                session.client.name if session.client else None
            ))
            session.skip = True

        if session.skip:
            session.save()
            return

        media_type = session.get_type()

        # Check if we are scrobbling a known media type
        if not media_type:
            Log.Info('Playing unknown item, will not be scrobbled: "%s"' % session.get_title())
            session.skip = True
            return

        session.last_view_offset = view_offset

        # Calculate progress
        if not self.update_progress(session, view_offset):
            Log.Warn('Error while updating session progress, queued session to be updated')
            session.update_required = True
            session.save()
            return

        action = self.get_action(session, state)

        if action:
            self.handle_action(session, media_type, action, state)
        else:
            Log.Debug('%s Nothing to do this time for %s' % (
                self.get_status_label(session, state),
                session.get_title()
            ))
            session.save()

        if self.handle_state(session, state) or action:
            session.save()
            Dict.Save()
