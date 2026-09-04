                    f"💰 الأرباح المحتملة (الإجمالي):\n"
                    f"   🎯 T1 `{plan['t1']}`: +`{plan['p1']:,.0f}` ج.م\n"
                    f"   🚀 T2 `{plan['t2']}`: +`{plan['p2']:,.0f}` ج.م\n"
                    f"   🔥 T3 `{plan['t3']}`: +`{plan['p3']:,.0f}` ج.م"
                    f"{fees_note}{risk_note}{rr_note}"
                )
                save_json_local(TRADES_FILE, trades)
                save_to_github(TRADES_FILE, trades, f"new trade {sym}")

            track(all_data, regime)
            eod_report(trades, all_data, cycle)
            if cycle < PULSE_CYCLES - 1:
                time.sleep(PULSE_SLEEP)

        eod_report(trades, all_data, PULSE_CYCLES - 1)
        save_to_github(TRADES_FILE, load_json_local(TRADES_FILE, {}), "trades sync")
        save_to_github(STATS_FILE, load_json_local(STATS_FILE, {}), "stats sync")
        logging.info("✅ اكتمل التشغيل بنجاح")

    finally:
        # [إصلاح #8] تحرير القفل دائمًا حتى لو حصل استثناء غير متوقع
        release_lock()

if __name__ == "__main__":
    run()
