# 無料版 USD/JPY 買い候補通知

TradingViewの有料テクニカルアラートを使わず、1時間ごとにUSD/JPYを判定してスマホへ通知します。発注は行いません。

## 使用する無料サービス

1. **Twelve Data**：USD/JPYの1時間足データ。無料アカウントとAPIキーが必要です。
2. **GitHub Actions**：毎時、自動で判定プログラムを動かします。
3. **ntfy**：スマホ通知。登録不要です。推測されにくい専用トピック名を使います。

## 1. ntfyを準備

1. スマホに `ntfy` アプリをインストールします。iPhoneはApp Store、AndroidはGoogle Playで検索できます。
2. アプリで「＋」を押し、購読するトピック名を入力します。
3. トピック名は、例えば `usdjpy-a8f3c1-自分だけの長い文字列` のように、他人が推測できないものにしてください。公開トピックなので、氏名やメールアドレスは含めません。
4. この名前を後で使うため控えます。

## 2. Twelve DataのAPIキーを取得

1. `https://twelvedata.com/` で無料アカウントを作成します。
2. DashboardのAPI Keyをコピーします。
3. APIキーは他人に送らず、公開ファイルにも書かないでください。

## 3. GitHubに置く

1. GitHubで新しいリポジトリを作ります。毎時実行なら公開リポジトリが無料枠を気にせず使えます。コードは公開されますが、後述のSecretsは公開されません。
2. このフォルダ内の `config.json`、`signal_monitor.py`、`.github/workflows/usdjpy-alert.yml` を、同じ配置のままアップロードします。
3. リポジトリの `Settings` → `Secrets and variables` → `Actions` を開きます。
4. `Secrets`で次の2つを作ります。
   - 名前：`TWELVE_DATA_API_KEY`／値：取得したAPIキー
   - 名前：`NTFY_TOPIC`／値：ntfyで決めたトピック名
5. `Variables`で次を作ります。
   - 名前：`SIGNAL_ENABLED`／値：`true`

## 4. テスト通知

1. GitHubのリポジトリで `Actions` を開きます。
2. 左側の `USDJPY alert` を選びます。
3. `Run workflow`を押し、テスト通知をオンのまま実行します。
4. スマホに「USD/JPY 通知テスト」と届けば完成です。

定期実行は毎時8分ごろです。GitHub側の混雑で遅れる場合があります。直近の確定済み1時間足だけを通知対象にするため、過去のシグナルがまとめて届くことはありません。条件不成立は見送りとして記録され、スマホには通知しません。

## 数値を変える

GitHub上で `config.json` を開き、鉛筆ボタンで編集します。

- `sma_length`: SMA期間（初期値20）
- `resistance_lookback`: レジスタンス算出本数（20）
- `max_pullback_bars`: 上抜け後に押し目を待つ本数（12）
- `retest_tolerance_pips`: 押し目の許容幅（5pips）
- `require_bullish_candle`: 陽線を必須にするなら `true`
- `stop_pips`: 損切り幅（30pips）
- `risk_reward`: リスクリワード（2.0）

編集後に `Commit changes` を押すと、次回の判定から反映されます。

## 重要指標前に停止・再開

`Settings` → `Secrets and variables` → `Actions` → `Variables`で、`SIGNAL_ENABLED`を変更します。

- 停止：`false`
- 再開：`true`

停止中も毎時の処理は起動しますが、データ取得と通知は行いません。米雇用統計、米CPI、FOMC、日銀会合などの前に停止し、値動きが落ち着いてから再開します。

## 注意事項

- 通知は売買推奨ではありません。通知後に指標、スプレッド、現在値を確認してください。
- 無料サービスの障害、遅延、仕様変更などにより通知が届かない場合があります。
- Twelve DataとTradingViewでは配信元が異なるため、ローソク足やシグナルに小さな差が出ます。
- APIキーと推測されにくいntfyトピック名は、必ずGitHub Secretsへ保存してください。
