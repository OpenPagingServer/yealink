# Yealink Push XML for Open Paging Server
The Yealink module for Open Paging Server can send visual messages via push XML over HTTP. This module does not send audio. Audio can be sent to Yealink IP phones either via Open Paging Server's built-in multicast RTP endpoint or via SIP.

## How to use
In your phones web admin interface, go to Features > Remote Control and enter in the following:

"Push XML Server IP Address": The IP address of the Open Paging Server of which the phone will see the request from

"User Name": Enter anything in this field

"Password": Create a strong password

Then click "Confirm"

In Open Paging Server, create a new Yealink Push XML endpoint with the IP address of the phone and the same username & password you provided to the phone earlier.

If you would like to provide audio to the phone, the most common way is to use Multicast RTP audio. If you are running Open Paging Server 0.4.0 or later, you can create a Multicast RTP endpoint, then add it under "Multicast Listening" under Directory > Multicast IP. You can also use a SIP trunk to a PBX registered to the phone and have OPS dial a paging or auto-answer group.
