import { Component, Input, OnChanges } from '@angular/core';

@Component({
  selector: 'app-orange-logo',
  template: `
    <div class="ol-box"
         [style.width.px]="size"
         [style.height.px]="size">

      <!-- "orange" text — bottom-left, bold, rounded -->
      <span class="ol-word"
            [style.fontSize.px]="fontSize"
            [style.paddingLeft.px]="pad"
            [style.paddingBottom.px]="padBottom">orange</span>

      <!-- ™ superscript — top-right of the "e" -->
      <span class="ol-tm"
            [style.fontSize.px]="tmSize"
            [style.top.px]="tmTop"
            [style.right.px]="tmRight">™</span>
    </div>
  `,
  styles: [`
    :host { display: inline-block; line-height: 0; }

    .ol-box {
      background: #FF7900;
      position: relative;
      display: inline-flex;
      align-items: flex-end;
      box-sizing: border-box;
      overflow: hidden;
      flex-shrink: 0;
    }

    .ol-word {
      font-family: 'Nunito', 'Arial Rounded MT Bold', 'Varela Round', Arial, sans-serif;
      font-weight: 900;
      color: #ffffff;
      line-height: 1;
      letter-spacing: -0.02em;
      white-space: nowrap;
      display: block;
      transform: scaleX(0.82);
      transform-origin: left center;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    .ol-tm {
      font-family: 'Nunito', 'Helvetica Neue', Arial, sans-serif;
      font-weight: 700;
      color: #ffffff;
      position: absolute;
      line-height: 1;
      -webkit-font-smoothing: antialiased;
    }
  `]
})
export class OrangeLogoComponent implements OnChanges {
  @Input() size = 60;

  fontSize   = 0;
  pad        = 0;
  padBottom  = 0;
  tmSize     = 0;
  tmTop      = 0;
  tmRight    = 0;

  ngOnChanges(): void {
    this.fontSize  = Math.round(this.size * 0.33);
    this.pad       = Math.round(this.size * 0.07);
    this.padBottom = Math.round(this.size * 0.07);
    this.tmSize    = Math.round(this.size * 0.13);
    this.tmTop     = Math.round(this.size * 0.16);
    this.tmRight   = Math.round(this.size * 0.04);
  }
}
