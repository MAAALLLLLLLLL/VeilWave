// VeilWave - Arduino Nano (ATmega328P) sampler v2: 16 kHz, 10 bit, packets, 250000 baud.
// NOT TESTED ON HARDWARE: compile, measure and check the Raspberry Pi parser statistics first.
//  - Timer1 compare match B triggers one ADC conversion every 62.5 us (16 kHz)
//  - ADC prescaler 64 -> 250 kHz ADC clock (above the 200 kHz recommended for full accuracy)
//  - packet = 0xA5, seq, 40 bytes (32 samples x 10 bit, 4 samples in 5 bytes)  = 42 bytes
//    500 packets/s = 21,000 bytes/s of the 25,000 bytes/s that 8N1 at 250000 baud carries (84 %)
//  - power the Nano from 5 V (16 MHz is out of specification at 3.3 V)
#include <Arduino.h>

#define BUF 256                              // ring buffer, power of two
volatile uint16_t buf[BUF];
volatile uint16_t head = 0;
uint16_t tail = 0;
uint8_t seqn = 0;

ISR(TIMER1_COMPB_vect) { }                   // only triggers the ADC
ISR(ADC_vect) { buf[head] = ADC; head = (head + 1) & (BUF - 1); }

void setup() {
  Serial.begin(250000);
  ADMUX = (1 << REFS0);                                  // AVcc reference, channel A0
  ADCSRB = (1 << ADTS2) | (1 << ADTS0);                  // trigger: Timer1 compare match B
  ADCSRA = (1 << ADEN) | (1 << ADATE) | (1 << ADIE) | (1 << ADPS2) | (1 << ADPS1);   // prescaler 64
  TCCR1A = 0;
  TCCR1B = (1 << WGM12) | (1 << CS11);                   // CTC, prescaler 8
  OCR1A = 124; OCR1B = 124;                              // 16e6 / (8 * 16000) - 1
  TIMSK1 = (1 << OCIE1B);
}

void loop() {
  uint16_t h;
  do { h = head; } while (h != head);
  if (((h - tail) & (BUF - 1)) >= 32) {
    uint8_t pkt[42];
    pkt[0] = 0xA5; pkt[1] = seqn++;
    for (uint8_t g = 0; g < 8; g++) {
      uint16_t a = buf[tail], b = buf[(tail + 1) & (BUF - 1)], c = buf[(tail + 2) & (BUF - 1)], d = buf[(tail + 3) & (BUF - 1)];
      tail = (tail + 4) & (BUF - 1);
      uint8_t *o = pkt + 2 + 5 * g;
      o[0] = a >> 2; o[1] = ((a & 3) << 6) | (b >> 4); o[2] = ((b & 15) << 4) | (c >> 6);
      o[3] = ((c & 63) << 2) | (d >> 8); o[4] = d & 255;
    }
    Serial.write(pkt, 42);
  }
}
