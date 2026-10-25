package io.github.murtazanasir.mailr.data.live

import io.github.murtazanasir.mailr.data.api.MailrApi

/** [LiveApi] over the mailr API (GET /live/{kind}/{id}, POST /live/typing). */
class MailrLiveApi(private val api: MailrApi) : LiveApi {
    override suspend fun item(kind: String, id: String): LiveItemState? =
        api.liveItem(kind, id).let { LiveItemState(it.version, it.content, it.by) }

    override suspend fun typing(kind: String, id: String) {
        api.liveTyping(kind, id)
    }
}
