#include <stdio.h>
#include <string.h>

int zent_send(int argc, char **argv);
int zent_recv(int argc, char **argv);
void zent_send_usage(FILE *stream);
void zent_recv_usage(FILE *stream);
void zent_send_examples(FILE *stream);
void zent_recv_examples(FILE *stream);

static void usage(FILE *stream) {
 fputs("zent — Zero-copy Efficient Network Transfer（ゼント）\n"
       "\n"
       "巨大なファイルの指定範囲を、TCPで別のマシンへ転送します。\n"
       "モードは最初の引数に指定してください。\n"
       "\n"
       "使い方:\n"
       "  zent --recv (--host HOST | --corr NUM | --bind IP) --port PORT --output FILE [オプション]\n"
       "  zent --send --input FILE --length SEC (--host HOST | --corr NUM) --port PORT [オプション]\n"
       "\n"
       "引数なし、または --help で全オプションの説明と使用例を表示します。\n", stream);
}

int main(int argc, char **argv) {
 if(argc==1 || (argc==2 && (!strcmp(argv[1],"--help") || !strcmp(argv[1],"-h")))) {
  usage(stdout);
  fputs("\n========== 送信オプション ==========\n\n", stdout);
  zent_send_usage(stdout);
  fputs("\n========== 受信オプション ==========\n\n", stdout);
  zent_recv_usage(stdout);
  fputs("\n========== 使用例と注意事項 ==========\n", stdout);
  zent_recv_examples(stdout);
  zent_send_examples(stdout);
  fputs("\nDRGスケジュールからのスクリプト生成: python mkzent.py --help\n"
        "通信は暗号化・認証されません。信頼できるネットワークで使用してください。\n", stdout);
  return 0;
 }
 if(argc>=2 && !strcmp(argv[1],"--send")) return zent_send(argc-1,argv+1);
 if(argc>=2 && !strcmp(argv[1],"--recv")) return zent_recv(argc-1,argv+1);
 usage(stderr); return 1;
}
