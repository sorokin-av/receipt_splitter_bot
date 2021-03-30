# receipt_splitter_bot

Splitting receipts between friends using telegram bot api and OCR.

Recognition works primarily for structured receipts (pubs, restaurants). 
Receipt item should match regexp specified in config.yml, i.e. "position name \s quantity \s summ" 


If receipt has QR-code it can be scanned and passed to Russian Federal Tax Service for obtaining its description. 
To do this, you have to register personal account in FTS and use its credentials for authentication.

Register your bot in BotFather, put token in credentials.env and run it.
